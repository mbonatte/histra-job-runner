from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from histra_runner.config import RunnerConfig, SolverConfig
from histra_runner.hrx import COMPLETED, TO_RUN, detect_xml_encoding, read_hrx, write_hrx
from histra_runner.jsonio import read_json, utc_now_iso
from histra_runner.runner import JobRunner
from histra_runner.solver import SolverExecution


class FakeSolver:
    def run(self, model_path: Path, timeout_seconds: float) -> SolverExecution:
        root = read_hrx(model_path)
        selected = None
        for analysis in root.iter("Analysis"):
            states = analysis.find("States")
            if states is None:
                continue
            if any(state.get("State") == TO_RUN for state in states.findall("State")):
                selected = analysis
                for state in states.findall("State"):
                    state.set("State", COMPLETED)
                    state.set("Exit", "Completed")
                    state.set("ExitDescription", "Fake solver completed")
                break
        assert selected is not None
        write_hrx(root, model_path, detect_xml_encoding(model_path))

        key = int(selected.get("Key"))
        db_path = model_path.with_suffix(".Results")
        connection = sqlite3.connect(db_path)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS DisplModelPoints (
                AnalysisKey INTEGER, IdElement INTEGER, ParentKey INTEGER,
                Step INTEGER, Ux REAL, Uy REAL, Uz REAL
            );
            CREATE TABLE IF NOT EXISTS ReactionSumStates (
                AnalysisKey INTEGER, Step INTEGER, R1 REAL, R2 REAL, R3 REAL
            );
            CREATE TABLE IF NOT EXISTS ModalValues (
                AnalysisKey INTEGER, Step INTEGER, Fn REAL,
                Mx_pcent REAL, My_pcent REAL, Mz_pcent REAL
            );
            """
        )
        connection.execute(
            "INSERT INTO DisplModelPoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, 101, 1, 1, 0.1 * key, 0.2 * key, 0.3 * key),
        )
        connection.execute(
            "INSERT INTO ReactionSumStates VALUES (?, ?, ?, ?, ?)",
            (key, 1, 1.0 * key, 2.0 * key, 3.0 * key),
        )
        connection.execute(
            "INSERT INTO ModalValues VALUES (?, ?, ?, ?, ?, ?)",
            (key, 1, 2.5, 50.0, 30.0, 20.0),
        )
        connection.commit()
        connection.close()
        now = utc_now_iso()
        return SolverExecution(
            model_path=model_path,
            command=("fake-solver", str(model_path)),
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            return_code=0,
            stdout=f"completed {selected.get('Name')}",
            stderr="",
        )


def write_model(path: Path) -> None:
    root = ET.fromstring(
        """
        <Root>
          <Analysis Name="StartMesh" Key="100" InitialAnalysisKey="-100">
            <States><State Id="1" State="NotExecutedNotToBeExecuted" /></States>
          </Analysis>
          <Analysis Name="Vert" Key="1" InitialAnalysisKey="-100">
            <States><State Id="1" State="NotExecutedNotToBeExecuted" /></States>
          </Analysis>
          <Analysis Name="LiveLoad_1" Key="2" InitialAnalysisKey="1">
            <States><State Id="1" State="NotExecutedNotToBeExecuted" /></States>
          </Analysis>
        </Root>
        """
    )
    ET.ElementTree(root).write(path, encoding="utf-16", xml_declaration=True)


def test_runner_executes_mesh_dependency_and_requested_analysis(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    write_model(package / "model.hrx")
    job = {
        "schema_version": "1.0",
        "job_id": "bridge-001",
        "model": {"path": "model.hrx"},
        "analyses": [
            {
                "name": "LiveLoad_1",
                "outputs": {
                    "reactions": {"all_steps": True},
                    "displacements": {"all_steps": True, "model_point_ids": [101]},
                    "modal_contributions": {"enabled": True, "top_n": 1},
                },
            }
        ],
    }
    (package / "job.json").write_text(json.dumps(job), encoding="utf-8")

    config = RunnerConfig(
        solver=SolverConfig(executable=tmp_path / "not-used.exe"),
        workspace_root=tmp_path / "work",
    )
    outcome = JobRunner(config, solver=FakeSolver()).run_job_file(package / "job.json")

    results = read_json(outcome.results_path)
    analysis = results["analyses"]["LiveLoad_1"]
    assert analysis["validation"]["completed"] is True
    assert analysis["outputs"]["reactions"][0]["R1"] == 2.0
    assert analysis["outputs"]["displacements"][0]["IdElement"] == 101
    assert analysis["outputs"]["modal_contributions"]["X"][0]["Mx_pcent"] == 50.0

    manifest = read_json(outcome.manifest_path)
    assert [item["command"][0] for item in manifest["executions"]] == [
        "fake-solver",
        "fake-solver",
        "fake-solver",
    ]
    assert (outcome.workspace / "run" / "model.Results").is_file()
    assert read_json(outcome.workspace / "state.json")["state"] == "completed"


def test_completed_dependency_is_preserved_and_not_rerun(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    write_model(package / "model.hrx")
    root = read_hrx(package / "model.hrx")
    for analysis in root.iter("Analysis"):
        if analysis.get("Name") == "Vert":
            for state in analysis.find("States").findall("State"):
                state.set("State", COMPLETED)
    write_hrx(root, package / "model.hrx", detect_xml_encoding(package / "model.hrx"))

    job = {
        "schema_version": "1.0",
        "job_id": "bridge-completed-dependency",
        "model": {"path": "model.hrx"},
        "mesh": {"enabled": False},
        "analyses": [{"name": "LiveLoad_1"}],
    }
    (package / "job.json").write_text(json.dumps(job), encoding="utf-8")
    config = RunnerConfig(
        solver=SolverConfig(executable=tmp_path / "not-used.exe"),
        workspace_root=tmp_path / "work",
    )
    outcome = JobRunner(config, solver=FakeSolver()).run_job_file(package / "job.json")
    manifest = read_json(outcome.manifest_path)
    assert len(manifest["executions"]) == 1
    final_root = read_hrx(outcome.workspace / "run" / "model.hrx")
    states = {
        analysis.get("Name"): analysis.find("States").find("State").get("State")
        for analysis in final_root.iter("Analysis")
    }
    assert states["Vert"] == COMPLETED
    assert states["LiveLoad_1"] == COMPLETED
