from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback
import uuid

from .backends import (
    AnalysisPlan,
    AnalysisPlanItem,
    CSharpBackend,
    MutationSchedule,
    OutputRequest,
    SolverBackend,
    SolverJobResult,
)
from .config import RunnerConfig
from .errors import JobRunError, SolverExecutionError
from .jsonio import utc_now_iso, write_json_atomic
from .schema import JobSpec, load_job_spec, sha256_file
from .solver import SolverClient
from .state import StateStore


@dataclass(frozen=True)
class RunOutcome:
    job_id: str
    attempt_id: str
    workspace: Path
    results_path: Path
    manifest_path: Path


class JobRunner:
    """Stage a package, delegate numerical work to a backend, and persist artifacts."""

    def __init__(
        self,
        config: RunnerConfig,
        solver: SolverClient | None = None,
        *,
        backend: SolverBackend | None = None,
    ):
        if solver is not None and backend is not None:
            raise ValueError("Pass either solver= or backend=, not both.")
        self.config = config
        if backend is not None:
            self.backend = backend
            self._require_solver_files = False
        else:
            self.backend = CSharpBackend(config.solver, solver=solver)
            self._require_solver_files = solver is None

    def run_job_file(self, job_path: str | Path) -> RunOutcome:
        resolved_job_path = Path(job_path).resolve()
        spec = load_job_spec(resolved_job_path)
        return self.run(spec, package_root=resolved_job_path.parent, source_job=resolved_job_path)

    def run(
        self,
        spec: JobSpec,
        *,
        package_root: Path,
        source_job: Path | None = None,
    ) -> RunOutcome:
        self.config.validate(
            require_solver_files=self._require_solver_files,
            validate_solver=self._require_solver_files or isinstance(self.backend, CSharpBackend),
        )
        self.backend.validate()
        attempt_id = spec.attempt_id or f"attempt-{uuid.uuid4().hex[:12]}"
        workspace = self.config.workspace_root / spec.job_id / attempt_id
        if workspace.exists():
            raise JobRunError(
                spec.job_id,
                workspace,
                FileExistsError("Attempt workspace already exists; use a new attempt_id."),
            )

        input_dir = workspace / "input"
        run_dir = workspace / "run"
        logs_dir = workspace / "logs"
        output_dir = workspace / "output"
        for directory in (input_dir, run_dir, logs_dir, output_dir):
            directory.mkdir(parents=True, exist_ok=False)

        state = StateStore(workspace / "state.json", spec.job_id, attempt_id)
        started_at = utc_now_iso()
        started = time.perf_counter()
        try:
            state.transition("validating")
            source_model = spec.validate_package(package_root)
            if source_job is not None:
                shutil.copy2(source_job, input_dir / "job.json")
            input_model = input_dir / source_model.name
            shutil.copy2(source_model, input_model)

            state.transition("staging")
            run_model = run_dir / "model.hrx"
            shutil.copy2(input_model, run_model)

            plan = self._analysis_plan(spec)
            mutations = MutationSchedule(
                interfaces_by_analysis={item.name: item.interfaces for item in spec.analyses},
                foundation_interface_materials=spec.scour.foundation_interface_materials,
                scoured_foundation_interface_material=spec.scour.scoured_foundation_interface_material,
            )
            outputs = OutputRequest(
                outputs_by_analysis={item.name: item.outputs for item in spec.analyses}
            )
            state.transition("running", backend=self.backend.name)
            backend_result = self.backend.run_job(
                run_model,
                plan,
                mutations,
                outputs,
                timeout_seconds=self._job_timeout(plan),
            )
            self._write_execution_logs(logs_dir, backend_result)

            state.transition("extracting")
            results_payload = {
                "schema_version": "1.0",
                "job_id": spec.job_id,
                "attempt_id": attempt_id,
                "analyses": {
                    name: {
                        "analysis_key": analysis.analysis_key,
                        "interfaces": analysis.interfaces,
                        "validation": analysis.validation,
                        "outputs": analysis.outputs,
                    }
                    for name, analysis in backend_result.analyses.items()
                },
            }
            results_path = output_dir / "results.json"
            write_json_atomic(results_path, results_payload)

            finished_at = utc_now_iso()
            manifest_path = output_dir / "run.json"
            write_json_atomic(
                manifest_path,
                self._manifest(
                    spec=spec,
                    attempt_id=attempt_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=time.perf_counter() - started,
                    input_model=input_model,
                    run_model=run_model,
                    backend_result=backend_result,
                    status="completed",
                ),
            )
            state.transition("completed", results=str(results_path.relative_to(workspace)))
            if not self.config.keep_raw_on_success:
                shutil.rmtree(run_dir, ignore_errors=True)
            return RunOutcome(spec.job_id, attempt_id, workspace, results_path, manifest_path)
        except Exception as exc:
            failure = {
                "job_id": spec.job_id,
                "attempt_id": attempt_id,
                "status": "failed",
                "failed_at": utc_now_iso(),
                "backend": self.backend.name,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            if isinstance(exc, SolverExecutionError):
                failed = exc.execution
                failure["solver"] = {
                    "command": list(failed.command),
                    "return_code": failed.return_code,
                    "timed_out": failed.timed_out,
                    "duration_seconds": round(failed.duration_seconds, 3),
                }
                (logs_dir / "failed-solver.stdout.log").write_text(
                    failed.stdout, encoding="utf-8", errors="replace"
                )
                (logs_dir / "failed-solver.stderr.log").write_text(
                    failed.stderr, encoding="utf-8", errors="replace"
                )
            write_json_atomic(output_dir / "failure.json", failure)
            (logs_dir / "traceback.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            state.transition("failed", error_type=type(exc).__name__, message=str(exc))
            if not self.config.keep_raw_on_failure:
                shutil.rmtree(run_dir, ignore_errors=True)
            raise JobRunError(spec.job_id, workspace, exc) from exc

    @staticmethod
    def _analysis_plan(spec: JobSpec) -> AnalysisPlan:
        return AnalysisPlan(
            analyses=tuple(
                AnalysisPlanItem(item.name, item.timeout_seconds) for item in spec.analyses
            ),
            mesh_analysis=(
                AnalysisPlanItem(spec.mesh.analysis_name, spec.mesh.timeout_seconds)
                if spec.mesh.enabled
                else None
            ),
            validation=spec.validation,
        )

    @staticmethod
    def _job_timeout(plan: AnalysisPlan) -> float:
        total = sum(item.timeout_seconds for item in plan.analyses)
        if plan.mesh_analysis is not None:
            total += plan.mesh_analysis.timeout_seconds
        return max(total, 1.0)

    @staticmethod
    def _write_execution_logs(logs_dir: Path, result: SolverJobResult) -> None:
        for index, item in enumerate(result.executions):
            safe_name = "".join(
                character if character.isalnum() or character in "._-" else "_"
                for character in item.analysis
            )
            prefix = f"{index:03d}-{safe_name}"
            (logs_dir / f"{prefix}.stdout.log").write_text(
                item.stdout, encoding="utf-8", errors="replace"
            )
            (logs_dir / f"{prefix}.stderr.log").write_text(
                item.stderr, encoding="utf-8", errors="replace"
            )

    @staticmethod
    def _manifest(
        *,
        spec: JobSpec,
        attempt_id: str,
        started_at: str,
        finished_at: str,
        duration_seconds: float,
        input_model: Path,
        run_model: Path,
        backend_result: SolverJobResult,
        status: str,
    ) -> dict:
        try:
            package_version = version("histra-job-runner")
        except PackageNotFoundError:
            package_version = "0.4.0+source"
        artifacts = {
            name: {
                "path": str(path),
                "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
            }
            for name, path in backend_result.artifacts.items()
        }
        payload = {
            "schema_version": "1.0",
            "job_id": spec.job_id,
            "attempt_id": attempt_id,
            "status": status,
            "backend": backend_result.backend,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(duration_seconds, 3),
            "runner": {
                "version": package_version,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "hostname": platform.node(),
            },
            "model": {
                "input_sha256": sha256_file(input_model),
                "final_sha256": sha256_file(run_model),
            },
            "artifacts": artifacts,
            "executions": [item.as_manifest_dict() for item in backend_result.executions],
            "mutations": list(backend_result.mutations),
            "validation": list(backend_result.validation),
            "metadata": spec.metadata,
            "backend_metadata": dict(backend_result.metadata),
        }
        results_database = backend_result.artifacts.get("results_database")
        if results_database is not None:
            payload["results_database"] = {
                "path": str(results_database.name),
                "size_bytes": (
                    results_database.stat().st_size if results_database.exists() else None
                ),
            }
        return payload
