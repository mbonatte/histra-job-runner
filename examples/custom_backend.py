"""Minimal example showing the future backend plug-in point."""
from pathlib import Path

from histra_runner.backends import (
    AnalysisPlan,
    MutationSchedule,
    OutputRequest,
    SolverJobResult,
)


class ExampleBackend:
    name = "example"

    def validate(self) -> None:
        pass

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        raise NotImplementedError("Replace with a real solver implementation")
