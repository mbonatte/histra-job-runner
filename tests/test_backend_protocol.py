from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from histra_runner.backends import (
    AnalysisJobResult,
    AnalysisPlan,
    AnalysisPlanItem,
    MutationSchedule,
    OutputRequest,
    SolverJobResult,
)
from histra_runner.runner import JobRunner
from histra_runner.schema import AnalysisOutputs, AnalysisSpec, JobSpec, MeshSpec, ModelSpec

from conftest import write_model


class RecordingBackend:
    name = "recording"

    def __init__(self) -> None:
        self.validated = False
        self.arguments = None

    def validate(self) -> None:
        self.validated = True

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        self.arguments = (model_path, analysis_plan, mutations, output_request, timeout_seconds)
        return SolverJobResult(
            backend=self.name,
            analyses={
                "A": AnalysisJobResult(
                    analysis_key=1,
                    interfaces={"changed": False, "piers": {}},
                    validation={"name": "A", "completed": True},
                    outputs={"reactions": []},
                )
            },
            executions=(),
            artifacts={"model": model_path},
        )


def test_job_runner_delegates_solver_work_to_backend(tmp_path, runner_config):
    model = tmp_path / "model.hrx"
    write_model(model, [("A", 1, -100, "NotExecutedNotToBeExecuted")])
    spec = JobSpec(
        schema_version="1.0",
        job_id="backend-job",
        attempt_id="attempt-1",
        model=ModelSpec("model.hrx"),
        analyses=(AnalysisSpec("A", timeout_seconds=10),),
        mesh=MeshSpec(enabled=False),
    )
    backend = RecordingBackend()
    # A non-C# backend must not be coupled to SolverConfig validation.
    neutral_config = replace(
        runner_config, solver=replace(runner_config.solver, mode="not-a-csharp-mode")
    )

    outcome = JobRunner(neutral_config, backend=backend).run(spec, package_root=tmp_path)

    assert backend.validated is True
    assert backend.arguments is not None
    _, plan, mutations, outputs, timeout = backend.arguments
    assert [item.name for item in plan.analyses] == ["A"]
    assert mutations.for_analysis("A") == {}
    assert outputs.analysis_names == ("A",)
    assert timeout == 10
    assert outcome.results_path.is_file()
    manifest = __import__("json").loads(outcome.manifest_path.read_text())
    assert manifest["backend"] == "recording"


def test_protocol_value_objects_are_backend_neutral():
    plan = AnalysisPlan((AnalysisPlanItem("A", 3.0),))
    schedule = MutationSchedule({"A": {"Pier_1": 0.5}}, ("Soil",), "Removed")
    request = OutputRequest({"A": AnalysisOutputs()})

    assert plan.timeout_for("A") == 3.0
    assert schedule.for_analysis("A") == {"Pier_1": 0.5}
    assert request.for_analysis("A") == AnalysisOutputs()
