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

from .config import RunnerConfig
from .errors import (
    HrxValidationError,
    JobRunError,
    SolverExecutionError,
)
from .extraction import extract_analysis_outputs
from .hrx import (
    analysis_evidence,
    analysis_key,
    dependency_order,
    select_only_analysis,
    validate_requested_analyses,
)
from .jsonio import utc_now_iso, write_json_atomic
from .schema import AnalysisSpec, JobSpec, load_job_spec, sha256_file
from .solver import SolverClient, SolverExecution, SubprocessSolver
from .state import StateStore


@dataclass(frozen=True)
class RunOutcome:
    job_id: str
    attempt_id: str
    workspace: Path
    results_path: Path
    manifest_path: Path


class JobRunner:
    """Execute one self-contained local job package.

    The runner has no knowledge of queues, tokens or HTTP. Network code can be
    added later as an adapter that downloads a package and calls this class.
    """

    def __init__(self, config: RunnerConfig, solver: SolverClient | None = None):
        self.config = config
        self.solver = solver or SubprocessSolver(config.solver)
        self._uses_default_solver = solver is None

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
        self.config.validate(require_solver_files=self._uses_default_solver)
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
        executions: list[dict] = []
        validation_evidence: list[dict] = []

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
            requested_names = [analysis.name for analysis in spec.analyses]
            names_to_validate = list(requested_names)
            if spec.mesh.enabled:
                names_to_validate.append(spec.mesh.analysis_name)
            validate_requested_analyses(run_model, names_to_validate)
            run_order = dependency_order(run_model, requested_names)

            if spec.mesh.enabled:
                state.transition("running", stage="mesh", analysis=spec.mesh.analysis_name)
                execution, evidence = self._execute_analysis(
                    run_model,
                    spec.mesh.analysis_name,
                    spec.mesh.timeout_seconds,
                    logs_dir,
                    stage_index=0,
                )
                executions.append(execution.as_dict())
                validation_evidence.append(evidence)
                self._require_completed_if_configured(evidence, spec)

            requested_by_name = {analysis.name: analysis for analysis in spec.analyses}
            default_timeout = max(analysis.timeout_seconds for analysis in spec.analyses)
            for index, name in enumerate(run_order, start=1):
                state.transition("running", stage="analysis", analysis=name)
                analysis_spec = requested_by_name.get(name)
                timeout = analysis_spec.timeout_seconds if analysis_spec else default_timeout
                execution, evidence = self._execute_analysis(
                    run_model,
                    name,
                    timeout,
                    logs_dir,
                    stage_index=index,
                )
                executions.append(execution.as_dict())
                validation_evidence.append(evidence)
                self._require_completed_if_configured(evidence, spec)

            results_database = run_model.with_suffix(".Results")
            if spec.validation.require_results_database:
                self._validate_results_database(results_database, spec.validation.minimum_results_bytes)

            state.transition("extracting")
            analysis_results: dict[str, dict] = {}
            for analysis_spec in spec.analyses:
                key = analysis_key(run_model, analysis_spec.name)
                evidence = analysis_evidence(run_model, analysis_spec.name)
                outputs = extract_analysis_outputs(
                    results_database, key, analysis_spec.outputs
                )
                analysis_results[analysis_spec.name] = {
                    "analysis_key": key,
                    "validation": evidence,
                    "outputs": outputs,
                }

            results_payload = {
                "schema_version": "1.0",
                "job_id": spec.job_id,
                "attempt_id": attempt_id,
                "analyses": analysis_results,
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
                    results_database=results_database,
                    executions=executions,
                    validation_evidence=validation_evidence,
                    status="completed",
                ),
            )
            state.transition("completed", results=str(results_path.relative_to(workspace)))

            if not self.config.keep_raw_on_success:
                shutil.rmtree(run_dir, ignore_errors=True)

            return RunOutcome(
                job_id=spec.job_id,
                attempt_id=attempt_id,
                workspace=workspace,
                results_path=results_path,
                manifest_path=manifest_path,
            )

        except Exception as exc:
            failure = {
                "job_id": spec.job_id,
                "attempt_id": attempt_id,
                "status": "failed",
                "failed_at": utc_now_iso(),
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

    def _execute_analysis(
        self,
        run_model: Path,
        analysis_name: str,
        timeout_seconds: float,
        logs_dir: Path,
        *,
        stage_index: int,
    ) -> tuple[SolverExecution, dict]:
        select_only_analysis(run_model, analysis_name)
        execution = self.solver.run(run_model, timeout_seconds)
        safe_name = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in analysis_name
        )
        prefix = f"{stage_index:03d}-{safe_name}"
        (logs_dir / f"{prefix}.stdout.log").write_text(
            execution.stdout, encoding="utf-8", errors="replace"
        )
        (logs_dir / f"{prefix}.stderr.log").write_text(
            execution.stderr, encoding="utf-8", errors="replace"
        )
        evidence = analysis_evidence(run_model, analysis_name)
        evidence["solver_return_code"] = execution.return_code
        evidence["solver_duration_seconds"] = round(execution.duration_seconds, 3)
        return execution, evidence

    @staticmethod
    def _require_completed_if_configured(evidence: dict, spec: JobSpec) -> None:
        if spec.validation.require_completed_state and not evidence.get("completed"):
            name = evidence.get("name", "<unknown>")
            states = [item.get("state") for item in evidence.get("states", [])]
            raise HrxValidationError(
                f"Analysis '{name}' did not finish with ExecutedCompleted states: {states}."
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
        results_database: Path,
        executions: list[dict],
        validation_evidence: list[dict],
        status: str,
    ) -> dict:
        try:
            package_version = version("histra-job-runner")
        except PackageNotFoundError:
            package_version = "0.1.0+source"
        return {
            "schema_version": "1.0",
            "job_id": spec.job_id,
            "attempt_id": attempt_id,
            "status": status,
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
            "results_database": {
                "path": str(results_database.name),
                "size_bytes": results_database.stat().st_size
                if results_database.exists()
                else None,
            },
            "executions": executions,
            "validation": validation_evidence,
            "metadata": spec.metadata,
        }
