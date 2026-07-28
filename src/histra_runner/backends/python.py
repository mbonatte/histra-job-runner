from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import time
from typing import Any

from ..config import PythonSolverConfig
from ..errors import ConfigurationError
from ..jsonio import utc_now_iso
from ..hrx import analyses_by_name, read_hrx
from ..scour import resolve_foundation_interface_mutations
from .protocol import (
    AnalysisJobResult,
    AnalysisPlan,
    BackendExecution,
    MutationSchedule,
    OutputRequest,
    SolverJobResult,
)


class PythonBackend:
    """Run the HiStrA Python package in-process without HRX/.Results state IO."""

    name = "python"

    def __init__(self, config: PythonSolverConfig):
        self.config = config

    def validate(self) -> None:
        try:
            package = import_module("histra")
        except ImportError as exc:
            raise ConfigurationError(
                "Python backend requires the 'histra-python' package. Install "
                "the Job Runner python-backend extra or install histra-python locally."
            ) from exc
        required = (0, 2, 0)
        raw = getattr(package, "__version__", None)
        if raw is None:
            try:
                raw = version("histra-python")
            except PackageNotFoundError:
                raw = "0.0.0"
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(raw))
        parsed = tuple(int(piece) for piece in match.groups()) if match else (0, 0, 0)
        if parsed < required:
            raise ConfigurationError(
                f"Python backend requires histra-python>=0.2.0; found {raw}."
            )

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        histra = import_module("histra")
        mutation_requests: dict[str, tuple[Any, ...]] = {}
        mutation_evidence: dict[str, dict[str, Any]] = {}
        analysis_elements = analyses_by_name(read_hrx(model_path))
        manifest_mutations: list[dict[str, Any]] = []
        for item in analysis_plan.analyses:
            scenario = mutations.for_analysis(item.name)
            resolution = resolve_foundation_interface_mutations(
                model_path,
                scenario,
                foundation_interface_materials=mutations.foundation_interface_materials,
                scoured_foundation_interface_material=(
                    mutations.scoured_foundation_interface_material
                ),
            )
            evidence = dict(resolution["evidence"])
            evidence["analysis"] = item.name
            mutation_evidence[item.name] = evidence
            manifest_mutations.append(evidence)
            analysis_element = analysis_elements.get(item.name)
            initial_key = (
                int(analysis_element.get("InitialAnalysisKey", "-100"))
                if analysis_element is not None
                else -100
            )
            preserve_committed_state = initial_key >= 0
            mutation_requests[item.name] = tuple(
                histra.ConcreteInterfaceMutation(
                    interface_keys=tuple(assignment["interface_keys"]),
                    material_key=int(assignment["material_key"]),
                    preserve_committed_state=preserve_committed_state,
                )
                for assignment in resolution["assignments"]
            )
            evidence["preserve_committed_state"] = preserve_committed_state

        requests = tuple(
            histra.PythonAnalysisRequest(
                name=item.name,
                output_request=output_request.for_analysis(item.name),
                timeout_seconds=item.timeout_seconds,
            )
            for item in analysis_plan.analyses
        )
        started_at = utc_now_iso()
        started = time.perf_counter()
        result = histra.run_python_solver_job(
            model_path,
            requests,
            timeout_seconds=timeout_seconds,
            interface_mutations=mutation_requests,
            combination_row=self.config.combination_row,
        )
        finished_at = utc_now_iso()
        total_duration = time.perf_counter() - started

        analyses: dict[str, AnalysisJobResult] = {}
        validations: list[dict[str, Any]] = []
        for name, item in result.analyses.items():
            execution = item.execution
            validation = {
                "name": name,
                "analysis_key": int(execution.analysis_key),
                "completed": bool(execution.completed),
                "outcome": execution.outcome.value if execution.outcome else None,
                "return_code": int(execution.code),
                "results_database": {
                    "required": False,
                    "reason": "Python backend projects outputs from in-memory committed states.",
                },
            }
            if analysis_plan.validation.require_completed_state and not execution.completed:
                raise RuntimeError(f"Python analysis {name!r} did not complete: {validation}")
            validations.append(validation)
            interfaces = dict(mutation_evidence.get(name, {"applied": False, "piers": {}}))
            interfaces["in_process_reports"] = [
                {
                    "interface_keys": [int(record.interface_key) for record in report.records],
                    "material_key": int(report.material_key),
                    "interface_count": int(report.interface_count),
                    "spring_count": int(report.spring_count),
                }
                for report in item.mutations
            ]
            analyses[name] = AnalysisJobResult(
                analysis_key=int(execution.analysis_key),
                interfaces=interfaces,
                validation=validation,
                outputs=dict(item.outputs),
            )

        executions = tuple(
            BackendExecution(
                backend=self.name,
                stage="analysis",
                analysis=execution.analysis_name,
                model_path=model_path,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=float(execution.runtime_seconds),
                status=(execution.outcome.value if execution.outcome else "unknown"),
                return_code=int(execution.code),
                command=("python", "in-process", execution.analysis_name),
                stdout="\n".join(result.logs),
                details={
                    "analysis_key": int(execution.analysis_key),
                    "committed_steps": len(execution.committed_steps),
                    "output_steps": len(execution.output_steps),
                },
            )
            for execution in result.executions
        )
        return SolverJobResult(
            backend=self.name,
            analyses=analyses,
            executions=executions,
            mutations=tuple(manifest_mutations),
            validation=tuple(validations),
            artifacts={"model": model_path},
            metadata={
                "histra_python_version": getattr(histra, "__version__", "unknown"),
                "combination_row": self.config.combination_row,
                "in_process": True,
                "duration_seconds": total_duration,
                "mesh_behavior": (
                    "HRX preprocessing is performed in-process when the model is loaded; "
                    "the C# StartMesh analysis is not executed."
                ),
            },
        )
