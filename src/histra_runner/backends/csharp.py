from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from ..config import SolverConfig
from ..errors import HrxValidationError
from ..extraction import extract_analysis_outputs
from ..hrx import (
    analysis_evidence,
    analysis_key,
    dependency_order,
    select_only_analysis,
    validate_requested_analyses,
)
from ..scour import run_update_foundation_ifaces
from ..solver import SolverClient, SubprocessSolver
from .protocol import (
    AnalysisJobResult,
    AnalysisPlan,
    BackendExecution,
    MutationSchedule,
    OutputRequest,
    SolverJobResult,
)


class CSharpBackend:
    """Existing SolverHistra.exe workflow behind the backend-neutral contract."""

    name = "csharp"

    def __init__(
        self,
        config: SolverConfig,
        *,
        solver: SolverClient | None = None,
        require_solver_files: bool | None = None,
    ):
        self.config = config
        self.solver = solver or SubprocessSolver(config)
        self.require_solver_files = (
            solver is None if require_solver_files is None else require_solver_files
        )

    def validate(self) -> None:
        self.config.validate(require_files=self.require_solver_files)

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        requested_names = [item.name for item in analysis_plan.analyses]
        names_to_validate = list(requested_names)
        if analysis_plan.mesh_analysis is not None:
            names_to_validate.append(analysis_plan.mesh_analysis.name)
        validate_requested_analyses(model_path, names_to_validate)

        run_order = dependency_order(model_path, requested_names)
        implicit_timeout = sum(
            analysis_plan.timeout_for(name)
            for name in run_order
            if name not in requested_names
        )
        # The caller budgets explicitly requested work. C# may need to execute
        # implicit HRX predecessors first, so grant those prerequisites their
        # historical per-analysis timeout without reducing the requested budget.
        deadline = time.monotonic() + timeout_seconds + implicit_timeout
        executions: list[BackendExecution] = []
        validation_evidence: list[dict[str, Any]] = []
        mutation_evidence: list[dict[str, Any]] = []
        mutation_by_analysis: dict[str, dict[str, Any]] = {}

        if analysis_plan.mesh_analysis is not None:
            mesh = analysis_plan.mesh_analysis
            execution, evidence = self._execute(
                model_path,
                mesh.name,
                self._effective_timeout(mesh.timeout_seconds, deadline),
                stage="mesh",
            )
            executions.append(execution)
            validation_evidence.append(evidence)
            self._require_completed(evidence, analysis_plan)

        for name in run_order:
            mutation = run_update_foundation_ifaces(
                model_path,
                mutations.for_analysis(name),
                foundation_interface_materials=mutations.foundation_interface_materials,
                scoured_foundation_interface_material=mutations.scoured_foundation_interface_material,
            )
            mutation["analysis"] = name
            mutation_evidence.append(mutation)
            mutation_by_analysis[name] = mutation

            execution, evidence = self._execute(
                model_path,
                name,
                self._effective_timeout(analysis_plan.timeout_for(name), deadline),
                stage="analysis",
            )
            executions.append(execution)
            validation_evidence.append(evidence)
            self._require_completed(evidence, analysis_plan)

        results_database = model_path.with_suffix(".Results")
        if analysis_plan.validation.require_results_database:
            self._validate_results_database(
                results_database, analysis_plan.validation.minimum_results_bytes
            )

        analyses: dict[str, AnalysisJobResult] = {}
        for name in output_request.analysis_names:
            key = analysis_key(model_path, name)
            evidence = analysis_evidence(model_path, name)
            analyses[name] = AnalysisJobResult(
                analysis_key=key,
                interfaces=mutation_by_analysis.get(name),
                validation=evidence,
                outputs=extract_analysis_outputs(
                    results_database, key, output_request.for_analysis(name)
                ),
            )

        return SolverJobResult(
            backend=self.name,
            analyses=analyses,
            executions=tuple(executions),
            mutations=tuple(mutation_evidence),
            validation=tuple(validation_evidence),
            artifacts={"results_database": results_database, "model": model_path},
        )

    @staticmethod
    def _effective_timeout(requested: float, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The job-wide backend timeout expired.")
        return min(requested, remaining)

    def _execute(
        self, model_path: Path, analysis_name: str, timeout_seconds: float, *, stage: str
    ) -> tuple[BackendExecution, dict[str, Any]]:
        select_only_analysis(model_path, analysis_name)
        execution = self.solver.run(model_path, timeout_seconds)
        evidence = analysis_evidence(model_path, analysis_name)
        evidence["solver_return_code"] = execution.return_code
        evidence["solver_duration_seconds"] = round(execution.duration_seconds, 3)
        return (
            BackendExecution(
                backend=self.name,
                stage=stage,
                analysis=analysis_name,
                model_path=execution.model_path,
                started_at=execution.started_at,
                finished_at=execution.finished_at,
                duration_seconds=execution.duration_seconds,
                status="completed",
                return_code=execution.return_code,
                command=execution.command,
                stdout=execution.stdout,
                stderr=execution.stderr,
                details={"validation": evidence},
            ),
            evidence,
        )

    @staticmethod
    def _require_completed(evidence: dict[str, Any], plan: AnalysisPlan) -> None:
        if plan.validation.require_completed_state and not evidence.get("completed"):
            states = [item.get("state") for item in evidence.get("states", [])]
            raise HrxValidationError(
                f"Analysis '{evidence.get('name', '')}' did not finish with "
                f"ExecutedCompleted states: {states}."
            )

    @staticmethod
    def _validate_results_database(path: Path, minimum_bytes: int) -> None:
        if not path.is_file():
            raise HrxValidationError(f"Expected results database was not created: {path}")
        size = path.stat().st_size
        if size < minimum_bytes:
            raise HrxValidationError(
                f"Results database is too small ({size} bytes; minimum {minimum_bytes})."
            )
