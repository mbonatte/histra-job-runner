from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
import pytest

from histra_runner.errors import BackendError
from histra_runner.histra_python_backend import (
    HiStrAPythonBackend,
    SolverApi,
    _jsonable,
)
from histra_runner.package import validate_package


@dataclass(frozen=True)
class FakeRequest:
    name: str
    output_request: object
    timeout_seconds: float


@dataclass(frozen=True)
class FakeMutation:
    interface_keys: tuple[int, ...]
    material_key: int
    preserve_committed_state: bool = True


class Outcome(StrEnum):
    COMPLETED = "completed"


@dataclass(frozen=True)
class FakeExecution:
    analysis_key: int
    analysis_name: str
    code: int = 0
    runtime_seconds: float = 1.25
    outcome: Outcome = Outcome.COMPLETED
    message: str | None = None
    steps: tuple[object, ...] = (object(), object())
    committed_steps: tuple[object, ...] = (object(),)
    completed: bool = True


@dataclass(frozen=True)
class FakeMutationReport:
    changed_interface_keys: tuple[int, ...]
    material_key: int


@dataclass(frozen=True)
class FakeAnalysisResult:
    execution: FakeExecution
    outputs: dict
    mutations: tuple[FakeMutationReport, ...] = ()


@dataclass(frozen=True)
class FakeJobResult:
    analyses: dict[str, FakeAnalysisResult]
    executions: tuple[FakeExecution, ...]
    logs: tuple[str, ...]
    model_path: Path
    metadata: dict = field(default_factory=lambda: {"serialized_in_process": True})


class SolverCapture:
    def __init__(self):
        self.kwargs = None
        self.raise_error = False
        self.nonfinite = False

    def run(self, model_path, analyses, **kwargs):
        self.kwargs = {
            "model_path": model_path,
            "analyses": tuple(analyses),
            **kwargs,
        }
        kwargs["on_log"]("solver started")
        if self.raise_error:
            raise RuntimeError("bad model")
        execution = FakeExecution(7, self.kwargs["analyses"][0].name)
        output = np.array([1.0, math.nan]) if self.nonfinite else np.array([1.0, 2.0])
        mutation_reports = tuple(
            FakeMutationReport(m.interface_keys, m.material_key)
            for m in (kwargs.get("interface_mutations") or {}).get(execution.analysis_name, ())
        )
        analysis = FakeAnalysisResult(
            execution,
            {"reactions": [{"step": 1, "reaction_x": output[0]}], "vector": output},
            mutation_reports,
        )
        return FakeJobResult(
            analyses={execution.analysis_name: analysis},
            executions=(execution,),
            logs=("solver started", "solver finished"),
            model_path=Path(model_path),
        )


def fake_api(capture: SolverCapture) -> SolverApi:
    return SolverApi(
        version="0.2.0-test",
        PythonAnalysisRequest=FakeRequest,
        ConcreteInterfaceMutation=FakeMutation,
        run_python_solver_job=capture.run,
    )


def contents(package_factory, job, hrx_bytes, tmp_path):
    package, _ = package_factory(job, hrx_bytes)
    return validate_package(package, tmp_path / "input")


def test_default_backend_executes_job_and_writes_inspectable_outputs(
    package_factory, job_document, hrx_bytes, tmp_path
):
    capture = SolverCapture()
    backend = HiStrAPythonBackend(solver_api=fake_api(capture))
    result = backend.execute(
        contents(package_factory, job_document, hrx_bytes, tmp_path),
        tmp_path / "output",
    )
    request = capture.kwargs["analyses"][0]
    assert request.name == "Static"
    assert request.timeout_seconds == 3600
    assert request.output_request.reactions.enabled is True
    assert request.output_request.displacements.enabled is True
    assert request.output_request.modal_contributions.enabled is False
    assert capture.kwargs["timeout_seconds"] == 3600
    assert capture.kwargs["combination_row"] == 1
    assert result.results["analyses"]["Static"]["outputs"]["vector"] == [1.0, 2.0]
    assert result.run["histra_python_version"] == "0.2.0-test"
    assert result.run["analysis_order"] == ["Static"]
    assert "solver finished" in result.logs
    assert json.loads((tmp_path / "output/results.json").read_text())["backend"] == "histra-python"
    assert (tmp_path / "output/run.json").exists()
    assert (tmp_path / "output/solver.log").exists()


def test_workflow_outputs_timeouts_and_mutations(
    package_factory, job_document, hrx_bytes, tmp_path
):
    job_document["workflow"] = {
        "timeout_seconds": 800,
        "analysis_timeout_seconds": 500,
        "combination_row": 3,
        "outputs": {
            "reactions": {"enabled": True, "all_steps": False, "step": 2},
            "displacements": {
                "enabled": True,
                "all_steps": True,
                "model_point_ids": [4, 9],
            },
        },
        "analyses": [
            {
                "name": "Scour",
                "timeout_seconds": 600,
                "interface_mutations": {
                    "interface_keys": [10, 11],
                    "material_key": 5,
                },
            }
        ],
        "interface_mutations": {
            "scour": [
                {
                    "interface_keys": [12],
                    "material_key": 6,
                    "preserve_committed_state": False,
                }
            ]
        },
    }
    capture = SolverCapture()
    result = HiStrAPythonBackend(solver_api=fake_api(capture)).execute(
        contents(package_factory, job_document, hrx_bytes, tmp_path),
        tmp_path / "output",
    )
    request = capture.kwargs["analyses"][0]
    assert request.timeout_seconds == 600
    assert request.output_request.reactions.step == 2
    assert request.output_request.reactions.all_steps is False
    assert request.output_request.displacements.model_point_ids == (4, 9)
    mutations = capture.kwargs["interface_mutations"]["Scour"]
    assert [m.interface_keys for m in mutations] == [(12,), (10, 11)]
    assert mutations[0].preserve_committed_state is False
    assert capture.kwargs["timeout_seconds"] == 800
    assert capture.kwargs["combination_row"] == 3
    reports = result.results["analyses"]["Scour"]["mutations"]
    assert reports[0]["changed_interface_keys"] == [12]


def test_multiple_analyses_infer_job_timeout(package_factory, job_document, hrx_bytes, tmp_path):
    job_document["workflow"] = {
        "analysis_timeout_seconds": 10,
        "analyses": ["Gravity", {"id": "Load", "timeout_seconds": 20}],
    }
    capture = SolverCapture()
    HiStrAPythonBackend(default_timeout_seconds=5, solver_api=fake_api(capture)).execute(
        contents(package_factory, job_document, hrx_bytes, tmp_path),
        tmp_path / "output",
    )
    assert [r.name for r in capture.kwargs["analyses"]] == ["Gravity", "Load"]
    assert capture.kwargs["timeout_seconds"] == 30


@pytest.mark.parametrize(
    ("workflow", "message"),
    [
        (None, "workflow must"),
        ({}, "analyses must"),
        ({"analyses": []}, "at least one"),
        ({"analyses": [{}]}, "name or id"),
        ({"analyses": ["A", "a"]}, "duplicate"),
        ({"analyses": ["A"], "combination_row": 0}, "at least 1"),
        ({"analyses": ["A"], "timeout_seconds": 0}, "positive"),
        ({"analyses": ["A"], "outputs": {"unknown": True}}, "unsupported output"),
        ({"analyses": ["A"], "outputs": {"reactions": {"all_steps": True, "step": 1}}}, "both"),
        ({"analyses": ["A"], "interface_mutations": {"B": {"interface_keys": [1], "material_key": 2}}}, "unrequested"),
        ({"analyses": [{"id": "A", "interface_mutations": {"interface_keys": [], "material_key": 2}}]}, "non-empty"),
        ({"analyses": [{"id": "A", "interface_mutations": {"interface_keys": [1, 1], "material_key": 2}}]}, "duplicates"),
        ({"analyses": [{"id": "A", "interface_mutations": {"interface_keys": [1]}}]}, "material_key is required"),
    ],
)
def test_invalid_workflows_are_rejected(
    workflow, message, package_factory, job_document, hrx_bytes, tmp_path
):
    job_document["workflow"] = workflow
    backend = HiStrAPythonBackend(solver_api=fake_api(SolverCapture()))
    with pytest.raises(BackendError, match=message):
        backend.execute(
            contents(package_factory, job_document, hrx_bytes, tmp_path),
            tmp_path / "output",
        )


def test_solver_errors_include_recent_log(package_factory, job_document, hrx_bytes, tmp_path):
    capture = SolverCapture()
    capture.raise_error = True
    with pytest.raises(BackendError, match="bad model") as error:
        HiStrAPythonBackend(solver_api=fake_api(capture)).execute(
            contents(package_factory, job_document, hrx_bytes, tmp_path),
            tmp_path / "output",
        )
    assert "solver started" in str(error.value)


def test_nonfinite_solver_output_is_rejected(package_factory, job_document, hrx_bytes, tmp_path):
    capture = SolverCapture()
    capture.nonfinite = True
    with pytest.raises(BackendError, match="non-finite"):
        HiStrAPythonBackend(solver_api=fake_api(capture)).execute(
            contents(package_factory, job_document, hrx_bytes, tmp_path),
            tmp_path / "output",
        )


def test_capabilities_and_json_conversion():
    backend = HiStrAPythonBackend(solver_api=fake_api(SolverCapture()))
    assert backend.capabilities["backend"] == "histra-python"
    assert backend.capabilities["histra_python_version"] == "0.2.0-test"
    assert _jsonable(np.int64(4)) == 4
    assert _jsonable(Path("x")) == "x"
    with pytest.raises(BackendError, match="unsupported"):
        _jsonable(object())
    with pytest.raises(BackendError, match="positive"):
        HiStrAPythonBackend(default_timeout_seconds=0, solver_api=fake_api(SolverCapture()))
