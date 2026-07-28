from __future__ import annotations

from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

import pytest

from histra_runner.config import RunnerConfig, SolverConfig
from histra_runner.hrx import COMPLETED, TO_RUN, detect_xml_encoding, read_hrx, write_hrx
from histra_runner.jsonio import utc_now_iso
from histra_runner.solver import SolverExecution


def write_model(
    path: Path,
    analyses: list[tuple[str, int, int, str]],
    *,
    with_scour_geometry: bool = False,
) -> None:
    root = ET.Element("Model")
    if with_scour_geometry:
        templates = ET.SubElement(root, "Templates")
        ET.SubElement(templates, "Template", Name="Foundation_Soil", Key="10")
        ET.SubElement(templates, "Template", Name="Soil_removed", Key="99")
        piers = ET.SubElement(root, "Piers")
        pier = ET.SubElement(
            piers,
            "Pier",
            Name="Pier_1",
            B1f="50",
            b2="0",
            B3f="50",
            W1f="10",
            w2="0",
            W3f="10",
            Hf="5",
        )
        ET.SubElement(pier, "ReferenceSystem", Origin="0;10;0")
        interfaces = ET.SubElement(root, "Interfaces")
        for key, x, y in [
            ("L", -40, 10),
            ("M", 0, 10),
            ("R", 40, 10),
            ("U", 0, 2),
            ("D", 0, 18),
        ]:
            ET.SubElement(
                interfaces,
                "Interface",
                Key=key,
                ParentTypeElement1="Restraint",
                MaterialKey="10",
                VInt3D1=f"{x};{y};-5",
            )
    container = ET.SubElement(root, "Analyses")
    for name, key, initial, state in analyses:
        analysis = ET.SubElement(
            container,
            "Analysis",
            Name=name,
            Key=str(key),
            InitialAnalysisKey=str(initial),
        )
        states = ET.SubElement(analysis, "States")
        ET.SubElement(states, "State", Id="0", State=state)
    write_hrx(root, path, "utf-16")


def ensure_results_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ReactionSumStates (
            AnalysisKey INTEGER, Step INTEGER, R1 REAL, R2 REAL, R3 REAL
        );
        CREATE TABLE IF NOT EXISTS DisplModelPoints (
            AnalysisKey INTEGER, IdElement INTEGER, ParentKey INTEGER,
            Step INTEGER, Ux REAL, Uy REAL, Uz REAL
        );
        CREATE TABLE IF NOT EXISTS ModalValues (
            AnalysisKey INTEGER, Step INTEGER, Fn REAL,
            Mx_pcent REAL, My_pcent REAL, Mz_pcent REAL
        );
        """
    )
    return connection


class FakeSolver:
    def __init__(self) -> None:
        self.executions: list[str] = []

    def run(self, model_path: Path, timeout_seconds: float) -> SolverExecution:
        root = read_hrx(model_path)
        selected = []
        for analysis in root.iter("Analysis"):
            states = list(analysis.iter("State"))
            if any(state.get("State") == TO_RUN for state in states):
                selected.append(analysis)
        assert len(selected) == 1
        analysis = selected[0]
        name = analysis.get("Name", "")
        key = int(analysis.get("Key", "0"))
        self.executions.append(name)
        for state in analysis.iter("State"):
            state.set("State", COMPLETED)
            state.set("Exit", "0")
            state.set("ExitDescription", "Completed")
        write_hrx(root, model_path, detect_xml_encoding(model_path))

        connection = ensure_results_database(model_path.with_suffix(".Results"))
        try:
            connection.execute(
                "INSERT INTO ReactionSumStates VALUES (?, ?, ?, ?, ?)",
                (key, 1, float(key), float(key + 1), float(key + 2)),
            )
            connection.execute(
                "INSERT INTO DisplModelPoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, 101, 1001, 1, key / 100.0, 0.0, 0.0),
            )
            connection.execute(
                "INSERT INTO ModalValues VALUES (?, ?, ?, ?, ?, ?)",
                (key, 1, 3.5, 50.0, 25.0, 10.0),
            )
            connection.commit()
        finally:
            connection.close()

        now = utc_now_iso()
        return SolverExecution(
            model_path=model_path,
            command=("fake-solver", "run", name),
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            return_code=0,
            stdout=f"ran {name}",
            stderr="",
        )


@pytest.fixture
def runner_config(tmp_path: Path) -> RunnerConfig:
    return RunnerConfig(
        solver=SolverConfig(executable=tmp_path / "SolverHistra.exe"),
        workspace_root=tmp_path / "work",
    )
