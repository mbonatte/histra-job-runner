from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import logging
import shutil
import time

from .config import NetworkWorkerConfig
from .contracts import (
    RUNNER_CAPABILITIES,
    RUNNER_VERSION,
    SUPPORTED_JOB_SCHEMA_VERSIONS,
    SUPPORTED_PACKAGE_PROTOCOLS,
)
from .errors import JobRunError, NetworkWorkerError, ServerRequestError
from .jsonio import read_json, utc_now_iso, write_json_atomic
from .network import Claim, ServerClient
from .runner import JobRunner
from .spool import AttemptRecord, AttemptSpool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    attempt_id: str
    local_status: str
    detail: str = ""


class NetworkWorker:
    """Pull jobs from the server and execute them through the local JobRunner."""

    def __init__(
        self,
        config: NetworkWorkerConfig,
        *,
        client: ServerClient | None = None,
        runner_factory: Callable[[], JobRunner] | None = None,
    ):
        self.config = config
        self.config.validate(require_solver_files=runner_factory is None)
        self.client = client or ServerClient(config.server)
        self._owns_client = client is None
        self.runner_factory = runner_factory or (lambda: JobRunner(config.runner))
        self.spool = AttemptSpool(config.worker.spool_root)
        self.worker_id: str | None = None
        self._stopped = False

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "NetworkWorker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def check_server(self) -> dict[str, Any]:
        return self.client.ready()

    def register(self) -> dict[str, Any]:
        response = self.client.register_worker(
            name=self.config.worker.name,
            max_parallel_jobs=self.config.worker.max_parallel_jobs,
            worker_version=RUNNER_VERSION,
            solver_version=self.config.worker.solver_version,
            metadata=self._worker_metadata(),
        )
        worker_id = response.get("id")
        if not isinstance(worker_id, str) or not worker_id:
            raise NetworkWorkerError("Worker registration returned no worker ID.")
        if response.get("enabled") is False:
            raise NetworkWorkerError("This worker is disabled on the server.")
        self.worker_id = worker_id
        write_json_atomic(
            self.config.worker.spool_root / "worker.json",
            {
                "worker_id": worker_id,
                "name": self.config.worker.name,
                "server_base_url": self.config.server.base_url,
                "registered_at": utc_now_iso(),
                "response": response,
            },
        )
        return response

    def stop(self) -> None:
        self._stopped = True

    def run_once(self) -> list[ProcessResult]:
        self.check_server()
        self.register()
        assert self.worker_id is not None
        records = list(self.spool.records())
        available = max(0, self.config.worker.max_parallel_jobs - len(records))
        for _ in range(available):
            claim = self.client.claim(self.worker_id)
            if claim is None:
                break
            records.append(
                self.spool.create(
                    claim,
                    worker_id=self.worker_id,
                    server_base_url=self.config.server.base_url,
                )
            )
        return [self._process_record(record) for record in records]

    def run_forever(self) -> None:
        while not self._stopped:
            try:
                self.run_once()
            except ServerRequestError as exc:
                logger.warning("Network worker poll failed: %s", exc)
            if not self._stopped:
                time.sleep(self.config.worker.poll_seconds)

    def _process_record(self, record: AttemptRecord) -> ProcessResult:
        claim = record.claim
        workspace = self.config.runner.workspace_root / claim.job_id / claim.attempt_id
        try:
            run_path = workspace / "output" / "run.json"
            if not record.job_path.is_file():
                record.transition("downloading")
                self.client.attempt_heartbeat(
                    claim, status="downloading", progress={"stage": "package"}
                )
                if not record.package_zip.is_file():
                    self.client.download_package(claim, record.package_zip)
                self.spool.extract_package(
                    record, maximum_bytes=self.config.server.maximum_package_bytes
                )
            if run_path.is_file():
                run_payload = read_json(run_path)
                if isinstance(run_payload, dict) and run_payload.get("status") == "completed":
                    return self._upload_completed(record, workspace)

            record.transition("running")
            self.client.attempt_heartbeat(
                claim, status="running", progress={"stage": "solver"}
            )
            outcome = self.runner_factory().run_job_file(record.job_path)
            record.transition("completed_local", workspace=str(outcome.workspace))
            return self._upload_completed(record, outcome.workspace)
        except JobRunError as exc:
            failure_path = exc.workspace / "output" / "failure.json"
            failure = read_json(failure_path) if failure_path.is_file() else {}
            return self._report_failure(record, exc.workspace, failure, exc)
        except ServerRequestError as exc:
            record.transition("network_pending", reason=str(exc))
            return ProcessResult(claim.job_id, claim.attempt_id, "network_pending", str(exc))
        except Exception as exc:
            return self._report_failure(record, workspace, {}, exc)

    def _upload_completed(self, record: AttemptRecord, workspace: Path) -> ProcessResult:
        results_path = workspace / "output" / "results.json"
        run_path = workspace / "output" / "run.json"
        if not results_path.is_file() or not run_path.is_file():
            raise NetworkWorkerError(f"Completed workspace is missing results files: {workspace}")
        run_payload = read_json(run_path)
        validation_path = record.directory / "validation.json"
        write_json_atomic(
            validation_path,
            {
                "schema_version": "1.0",
                "job_id": record.claim.job_id,
                "attempt_id": record.claim.attempt_id,
                "validation": run_payload.get("validation", []),
                "mutations": run_payload.get("mutations", []),
            },
        )
        solver_log = self._combine_solver_logs(workspace, record.directory / "solver.log")
        record.transition("uploading")
        self.client.attempt_heartbeat(
            record.claim, status="uploading", progress={"stage": "results"}
        )
        response = self.client.upload_results(
            record.claim,
            results_path=results_path,
            run_path=run_path,
            validation_path=validation_path,
            solver_log_path=solver_log,
        )
        record.transition(
            "accepted",
            server_job_status=response.get("status"),
            accepted_at=utc_now_iso(),
        )
        if self.config.worker.cleanup_package_on_accept:
            self.spool.remove_package(record)
        if self.config.worker.cleanup_workspace_on_accept:
            shutil.rmtree(workspace, ignore_errors=True)
        return ProcessResult(
            record.claim.job_id,
            record.claim.attempt_id,
            "accepted",
            "Server accepted results.",
        )

    def _report_failure(
        self,
        record: AttemptRecord,
        workspace: Path,
        failure: dict[str, Any],
        exc: Exception,
    ) -> ProcessResult:
        reason = str(failure.get("message") or exc)
        solver = failure.get("solver") if isinstance(failure.get("solver"), dict) else {}
        exit_code = solver.get("return_code") if isinstance(solver.get("return_code"), int) else None
        record.transition("failure_local", reason=reason)
        try:
            response = self.client.report_failure(
                record.claim,
                reason=reason,
                retryable=bool(failure.get("retryable", True)),
                exit_code=exit_code,
                run=failure or None,
                validation={"workspace": str(workspace)},
            )
        except ServerRequestError as network_error:
            record.transition("failure_local", report_error=str(network_error))
            return ProcessResult(
                record.claim.job_id,
                record.claim.attempt_id,
                "failure_local",
                str(network_error),
            )
        record.transition(
            "failure_reported",
            server_job_status=response.get("status"),
            reported_at=utc_now_iso(),
        )
        return ProcessResult(
            record.claim.job_id, record.claim.attempt_id, "failure_reported", reason
        )

    @staticmethod
    def _combine_solver_logs(
        workspace: Path, destination: Path, *, maximum_bytes: int = 10 * 1024 * 1024
    ) -> Path | None:
        logs = sorted((workspace / "logs").glob("*.log"))
        if not logs:
            return None
        written = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            for path in logs:
                header = f"\n===== {path.name} =====\n".encode()
                if written + len(header) > maximum_bytes:
                    break
                output.write(header)
                written += len(header)
                data = path.read_bytes()[: maximum_bytes - written]
                output.write(data)
                written += len(data)
                if written >= maximum_bytes:
                    break
        return destination

    def _worker_metadata(self) -> dict[str, Any]:
        metadata = dict(self.config.worker.metadata)
        existing = metadata.get("capabilities", [])
        if isinstance(existing, str):
            existing = [existing]
        metadata.update(
            {
                "workspace_root": str(self.config.runner.workspace_root),
                "spool_root": str(self.config.worker.spool_root),
                "protocol_versions": sorted(SUPPORTED_PACKAGE_PROTOCOLS),
                "job_schema_versions": sorted(SUPPORTED_JOB_SCHEMA_VERSIONS),
                "capabilities": sorted(set(RUNNER_CAPABILITIES) | {str(x) for x in existing}),
            }
        )
        return metadata
