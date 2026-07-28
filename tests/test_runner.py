from __future__ import annotations

import json
from pathlib import Path

from histra_runner.hrx import COMPLETED, NOT_TO_RUN, read_hrx
from histra_runner.runner import JobRunner
from histra_runner.schema import (
    AnalysisOutputs,
    AnalysisSpec,
    JobSpec,
    MeshSpec,
    ModalOutputSpec,
    ModelSpec,
)

from conftest import FakeSolver, write_model


def test_csharp_refactor_preserves_existing_execution_and_results(tmp_path, runner_config):
    model = tmp_path / "bridge.hrx"
    write_model(
        model,
        [
            ("StartMesh", 100, -100, NOT_TO_RUN),
            ("Vert", 1, -100, NOT_TO_RUN),
            ("LiveLoad_1", 2, 1, NOT_TO_RUN),
        ],
    )
    spec = JobSpec(
        schema_version="1.0",
        job_id="job-1",
        attempt_id="attempt-1",
        model=ModelSpec("bridge.hrx"),
        analyses=(
            AnalysisSpec(
                "LiveLoad_1",
                timeout_seconds=30,
                outputs=AnalysisOutputs(modal_contributions=ModalOutputSpec(True, 1)),
            ),
        ),
    )
    fake = FakeSolver()

    outcome = JobRunner(runner_config, solver=fake).run(spec, package_root=tmp_path)

    assert fake.executions == ["StartMesh", "Vert", "LiveLoad_1"]
    payload = json.loads(outcome.results_path.read_text())
    live = payload["analyses"]["LiveLoad_1"]
    assert live["validation"]["completed"] is True
    assert live["outputs"]["reactions"][0]["R1"] == 2.0
    assert live["outputs"]["displacements"][0]["IdElement"] == 101
    assert live["outputs"]["modal_contributions"]["X"][0]["Mx_pcent"] == 50.0

    manifest = json.loads(outcome.manifest_path.read_text())
    assert manifest["backend"] == "csharp"
    assert [item["analysis"] for item in manifest["executions"]] == fake.executions
    assert all(item["command"][0] == "fake-solver" for item in manifest["executions"])
    assert (outcome.workspace / "run" / "model.Results").is_file()
    state = json.loads((outcome.workspace / "state.json").read_text())
    assert state["state"] == "completed"


def test_completed_dependency_is_not_reexecuted(tmp_path, runner_config):
    model = tmp_path / "bridge.hrx"
    write_model(
        model,
        [
            ("Vert", 1, -100, COMPLETED),
            ("Live", 2, 1, NOT_TO_RUN),
        ],
    )
    spec = JobSpec(
        schema_version="1.0",
        job_id="job-completed-dep",
        attempt_id="attempt-1",
        model=ModelSpec("bridge.hrx"),
        analyses=(AnalysisSpec("Live"),),
        mesh=MeshSpec(enabled=False),
    )
    fake = FakeSolver()

    outcome = JobRunner(runner_config, solver=fake).run(spec, package_root=tmp_path)

    assert fake.executions == ["Live"]
    final_root = read_hrx(outcome.workspace / "run" / "model.hrx")
    vert_state = next(
        analysis for analysis in final_root.iter("Analysis") if analysis.get("Name") == "Vert"
    ).find("States/State")
    assert vert_state is not None and vert_state.get("State") == COMPLETED
