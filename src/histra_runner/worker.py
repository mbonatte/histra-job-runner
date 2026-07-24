from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event, Lock, Thread
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
from .errors import (
    JobRunError,
    LeaseLostError,
    NetworkWorkerError,
    PackageError,
    ServerRequestError,
)
from .jsonio import read_json, utc_now_iso, write_json_atomic
from .network import Claim, ServerClient
from .runner import JobRunner, RunOutcome
from .spool import AttemptRecord, AttemptSpool

logger = logging.getLogger(__name__)


def _package_version() -> str:
    try:
        return version("histra-job-runner")
    except PackageNotFoundError:
        return f"{RUNNER_VERSION}+source


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = read_json(path)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _workspace_state(workspace: Path) -> tuple[str, dict[str, Any]]:
    state = _read_json_object(workspace / "state.json") or {}
    runner_state = str(state.get("state", "running"))
    status = "extracting" if runner_state == "extracting" else "running"
    progress: dict[str, Any] = {"runner_state": runner_state}
    history = state.get("history")
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, dict) and isinstance(last.get("details"), dict):
            progress.update(last["details"])
    return status, progress


class AttemptHeartbeatLoop:
    def __init__(
        self,
        client: ServerClient,
        claim: Claim,
        workspace: Path,
        interval_seconds: float,
    ):
        self.client = client
        self.claim = claim
        self.workspace = workspace
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._lost = Event()
        self._lock = Lock()
        self._override: tuple[str, dict[str, Any]] | None = None
        self._thread = Thread(
            target=self._run,
            name=f"heartbeat-{claim.attempt_id[:8]}",
            daemon=True,
        )

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def set_phase(self, status: str, **progress: Any) -> None:
        with self._lock:
            self._override = (status, progress)

    def clear_phase(self) -> None:
        with self._lock:
            self._override = None

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds + 1.0))

    def _current(self) -> tuple[str, dict[str, Any]]:
        with self._lock:
            if self._override is not None:
                return self._override
        return _workspace_state(self.workspace)

    def _run(self) -> None:
        while not self._stop.is_set():
            status, progress = self._current()
            try:
                self.client.attempt_heartbeat(
                    self.claim,
                    status=status,
                    progress=progress,
                )
            except LeaseLostError as exc:
                logger.error(
                    "Lease lost for %s/%s: %s",
                    self.claim.job_id,
                    self.claim.attempt_id,
                    exc,
                )
                self._lost.set()
                return
            except ServerRequestError as exc:
                # Temporary loss of the server must not terminate HiStrA.
                logger.warning(
                    "Heartbeat failed for %s/%s; local execution continues: %s",
                    self.claim.job_id,
                    self.claim.attempt_id,
                    exc,
                )
            self._stop.wait(self.interval_seconds)


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    attempt_id: str
    local_status: str
    detail: str


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
        self.spool = AttemptSpool(config.worker.spool_root)
        self.runner_factory = runner_factory or (lambda: JobRunner(config.runner))
        self.worker_id: str | None = None
        self._stop = Event()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "NetworkWorker":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def check_server(self) -> dict[str, Any]:
        return self.client.ready()

    def register(self) -> dict[str, Any]:
        response = self.client.register_worker(
            name=self.config.worker.name,
            max_parallel_jobs=self.config.worker.max_parallel_jobs,
            worker_version=_package_version(),
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
        self._stop.set()

    def run_once(self) -> list[ProcessResult]:
        """Recover pending attempts, then claim at most one capacity-sized batch."""
        self.check_server()
        self.register()
        assert self.worker_id is not None

        records = self._recoverable_records(self.worker_id)
        capacity = self.config.worker.max_parallel_jobs
        claims_allowed = max(0, capacity - len(records))
        for _ in range(claims_allowed):
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
        return self._process_records(records, capacity=capacity)

    def run_forever(self) -> None:
        self.check_server()
        self.register()
        assert self.worker_id is not None
        capacity = self.config.worker.max_parallel_jobs
        pending_queue = deque(self._recoverable_records(self.worker_id))
        last_worker_heartbeat = 0.0

        with ThreadPoolExecutor(
            max_workers=capacity,
            thread_name_prefix="histra-job",
        ) as executor:
            futures: dict[Future[ProcessResult], AttemptRecord] = {}
            while pending_queue and len(futures) < capacity:
                record = pending_queue.popleft()
                futures[executor.submit(self._process_record, record)] = record

            try:
                while not self._stop.is_set():
                    for future in list(futures):
                        if not future.done():
                            continue
                        record = futures.pop(future)
                        try:
                            result = future.result()
                            logger.info(
                                "%s/%s -> %s: %s",
                                result.job_id,
                                result.attempt_id,
                                result.local_status,
                                result.detail,
                            )
                        except Exception:
                            logger.exception(
                                "Unhandled attempt error for %s/%s",
                                record.claim.job_id,
                                record.claim.attempt_id,
                            )

                    now = time.monotonic()
                    if now - last_worker_heartbeat >= self.config.worker.worker_heartbeat_seconds:
                        try:
                            self.client.worker_heartbeat(
                                self.worker_id,
                                max_parallel_jobs=capacity,
                                worker_version=_package_version(),
                                solver_version=self.config.worker.solver_version,
                                metadata=self._worker_metadata(running_jobs=len(futures)),
                            )
                        except ServerRequestError as exc:
                            logger.warning("Worker heartbeat failed: %s", exc)
                        last_worker_heartbeat = now

                    while len(futures) < capacity and not self._stop.is_set():
                        if pending_queue:
                            record = pending_queue.popleft()
                            futures[executor.submit(self._process_record, record)] = record
                            continue
                        try:
                            claim = self.client.claim(self.worker_id)
                        except ServerRequestError as exc:
                            logger.warning("Could not claim a job: %s", exc)
                            break
                        if claim is None:
                            break
                        record = self.spool.create(
                            claim,
                            worker_id=self.worker_id,
                            server_base_url=self.config.server.base_url,
                        )
                        futures[executor.submit(self._process_record, record)] = record

                    self._stop.wait(self.config.worker.poll_seconds)
            except KeyboardInterrupt:
                logger.info("Stopping new claims; waiting for active HiStrA jobs.")
                self._stop.set()

            for future, record in list(futures.items()):
                try:
                    result = future.result()
                    logger.info(
                        "%s/%s -> %s: %s",
                        result.job_id,
                        result.attempt_id,
                        result.local_status,
                        result.detail,
                    )
                except Exception:
                    logger.exception(
                        "Unhandled attempt error for %s/%s",
                        record.claim.job_id,
                        record.claim.attempt_id,
                    )

    def _process_records(
        self, records: list[AttemptRecord], *, capacity: int
    ) -> list[ProcessResult]:
        if not records:
            return []
        results: list[ProcessResult] = []
        with ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="histra-job") as executor:
            future_map = {
                executor.submit(self._process_record, record): record for record in records
            }
            for future, record in list(future_map.items()):
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.exception(
                        "Unhandled attempt error for %s/%s",
                        record.claim.job_id,
                        record.claim.attempt_id,
                    )
                    results.append(
                        ProcessResult(
                            record.claim.job_id,
                            record.claim.attempt_id,
                            "error",
                            str(exc),
                        )
                    )
        return results

    def _recoverable_records(self, worker_id: str) -> list[AttemptRecord]:
        records: list[AttemptRecord] = []
        for record in self.spool.records():
            if record.server_base_url.rstrip("/") != self.config.server.base_url.rstrip("/"):
                logger.warning(
                    "Ignoring pending attempt from another server: %s/%s (%s)",
                    record.claim.job_id,
                    record.claim.attempt_id,
                    record.server_base_url,
                )
                continue
            if record.worker_id != worker_id:
                logger.warning(
                    "Ignoring pending attempt owned by worker ID %s: %s/%s",
                    record.worker_id,
                    record.claim.job_id,
                    record.claim.attempt_id,
                )
                continue
            records.append(record)
        return records

    def _process_record(self, record: AttemptRecord) -> ProcessResult:
        claim = record.claim
        workspace = (
            self.config.runner.workspace_root / claim.job_id / claim.attempt_id
        )
        run_payload = _read_json_object(workspace / "output" / "run.json")
        failure_payload = _read_json_object(workspace / "output" / "failure.json")

        if run_payload and run_payload.get("status") == "completed":
            return self._upload_completed(record, workspace)
        if failure_payload:
            return self._report_failure(record, workspace, failure_payload)
        if workspace.exists():
            interrupted = {
                "job_id": claim.job_id,
                "attempt_id": claim.attempt_id,
                "status": "failed",
                "error_type": "InterruptedAttempt",
                "message": "Worker restarted while this local attempt was incomplete.",
            }
            record.transition("failure_local", reason=interrupted["message"])
            return self._report_failure(record, workspace, interrupted)

        try:
            if not record.job_path.is_file():
                record.transition("downloading")
                self.client.attempt_heartbeat(
                    claim,
                    status="downloading",
                    progress={"stage": "package"},
                )
                if not record.package_zip.is_file():
                    self.client.download_package(claim, record.package_zip)
                AttemptSpool.extract_package(
                    record,
                    maximum_bytes=self.config.server.maximum_package_bytes,
                )

            record.transition("running")
            heartbeat = AttemptHeartbeatLoop(
                self.client,
                claim,
                workspace,
                self.config.worker.heartbeat_seconds,
            )
            heartbeat.start()
            try:
                outcome = self.runner_factory().run_job_file(record.job_path)
                record.transition(
                    "completed_local",
                    workspace=str(outcome.workspace),
                    results=str(outcome.results_path),
                )
                heartbeat.set_phase("uploading", stage="results")
                result = self._upload_completed(record, outcome.workspace)
                if heartbeat.lease_lost and result.local_status != "accepted":
                    record.transition("orphaned", reason="Lease was lost during execution.")
                return result
            except JobRunError as exc:
                failure = _read_json_object(exc.workspace / "output" / "failure.json") or {
                    "job_id": claim.job_id,
                    "attempt_id": claim.attempt_id,
                    "status": "failed",
                    "error_type": type(exc.cause).__name__,
                    "message": str(exc.cause),
                }
                record.transition("failure_local", reason=str(failure.get("message", exc)))
                heartbeat.set_phase("uploading", stage="failure")
                return self._report_failure(record, exc.workspace, failure)
            finally:
                heartbeat.stop()

        except LeaseLostError as exc:
            record.transition("orphaned", reason=str(exc))
            return ProcessResult(claim.job_id, claim.attempt_id, "orphaned", str(exc))
        except PackageError as exc:
            record.transition("failure_local", reason=str(exc))
            return self._report_failure(
                record,
                workspace,
                {
                    "job_id": claim.job_id,
                    "attempt_id": claim.attempt_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "retryable": False,
                },
            )
        except ServerRequestError as exc:
            # Leave the record pending so the next start/poll can recover it.
            record.transition("network_pending", reason=str(exc))
            return ProcessResult(claim.job_id, claim.attempt_id, "network_pending", str(exc))
        except Exception as exc:
            logger.exception("Unexpected local worker failure")
            record.transition("failure_local", reason=str(exc))
            return self._report_failure(
                record,
                workspace,
                {
                    "job_id": claim.job_id,
                    "attempt_id": claim.attempt_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "retryable": True,
                },
            )

    def _upload_completed(self, record: AttemptRecord, workspace: Path) -> ProcessResult:
        claim = record.claim
        results_path = workspace / "output" / "results.json"
        run_path = workspace / "output" / "run.json"
        if not results_path.is_file() or not run_path.is_file():
            raise NetworkWorkerError(f"Completed workspace is missing results files: {workspace}")

        validation_path = record.directory / "validation.json"
        run_payload = _read_json_object(run_path) or {}
        write_json_atomic(
            validation_path,
            {
                "schema_version": "1.0",
                "job_id": claim.job_id,
                "attempt_id": claim.attempt_id,
                "validation": run_payload.get("validation", []),
                "mutations": run_payload.get("mutations", []),
            },
        )
        solver_log_path = self._combine_solver_logs(workspace, record.directory / "solver.log")
        record.transition("uploading")
        try:
            self.client.attempt_heartbeat(
                claim,
                status="uploading",
                progress={"stage": "results"},
            )
            response = self.client.upload_results(
                claim,
                results_path=results_path,
                run_path=run_path,
                validation_path=validation_path,
                solver_log_path=solver_log_path,
            )
        except LeaseLostError as exc:
            record.transition("orphaned", reason=str(exc))
            return ProcessResult(claim.job_id, claim.attempt_id, "orphaned", str(exc))
        except ServerRequestError as exc:
            record.transition("completed_local", upload_error=str(exc))
            return ProcessResult(
                claim.job_id,
                claim.attempt_id,
                "completed_local",
                f"Upload pending: {exc}",
            )

        record.transition(
            "accepted",
            server_job_status=response.get("status"),
            accepted_at=utc_now_iso(),
        )
        self._cleanup_accepted(record, workspace)
        return ProcessResult(claim.job_id, claim.attempt_id, "accepted", "Server accepted results.")

    def _report_failure(
        self,
        record: AttemptRecord,
        workspace: Path,
        failure: dict[str, Any],
    ) -> ProcessResult:
        claim = record.claim
        reason = str(failure.get("message") or failure.get("reason") or "Local job failed")
        retryable = bool(failure.get("retryable", True))
        solver = failure.get("solver") if isinstance(failure.get("solver"), dict) else {}
        exit_code = solver.get("return_code")
        if not isinstance(exit_code, int):
            exit_code = None
        try:
            response = self.client.report_failure(
                claim,
                reason=reason,
                retryable=retryable,
                exit_code=exit_code,
                run=failure,
                validation={"workspace": str(workspace)},
            )
        except LeaseLostError as exc:
            record.transition("orphaned", reason=str(exc))
            return ProcessResult(claim.job_id, claim.attempt_id, "orphaned", str(exc))
        except ServerRequestError as exc:
            record.transition("failure_local", report_error=str(exc))
            return ProcessResult(
                claim.job_id,
                claim.attempt_id,
                "failure_local",
                f"Failure report pending: {exc}",
            )
        record.transition(
            "failure_reported",
            server_job_status=response.get("status"),
            reported_at=utc_now_iso(),
        )
        return ProcessResult(claim.job_id, claim.attempt_id, "failure_reported", reason)

    def _cleanup_accepted(self, record: AttemptRecord, workspace: Path) -> None:
        if self.config.worker.cleanup_package_on_accept:
            AttemptSpool.remove_package(record)
        if self.config.worker.cleanup_workspace_on_accept:
            shutil.rmtree(workspace, ignore_errors=True)

    @staticmethod
    def _combine_solver_logs(
        workspace: Path,
        destination: Path,
        *,
        maximum_bytes: int = 10 * 1024 * 1024,
    ) -> Path | None:
        logs = sorted((workspace / "logs").glob("*.log"))
        if not logs:
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with destination.open("wb") as output:
            for path in logs:
                header = f"\n===== {path.name} =====\n".encode("utf-8")
                if written + len(header) > maximum_bytes:
                    break
                output.write(header)
                written += len(header)
                with path.open("rb") as source:
                    while written < maximum_bytes:
                        chunk = source.read(min(64 * 1024, maximum_bytes - written))
                        if not chunk:
                            break
                        output.write(chunk)
                        written += len(chunk)
                if written >= maximum_bytes:
                    output.write(b"\n[combined log truncated]\n")
                    break
        return destination

    def _worker_metadata(self, *, running_jobs: int = 0) -> dict[str, Any]:
        metadata = dict(self.config.worker.metadata)
        metadata.update(
            {
                "running_jobs": running_jobs,
                "workspace_root": str(self.config.runner.workspace_root),
                "spool_root": str(self.config.worker.spool_root),
                "protocol_versions": sorted(SUPPORTED_PACKAGE_PROTOCOLS),
                "job_schema_versions": sorted(SUPPORTED_JOB_SCHEMA_VERSIONS),
                "capabilities": sorted(
                    set(RUNNER_CAPABILITIES)
                    | {
                        str(item)
                        for item in (
                            [metadata.get("capabilities")]
                            if isinstance(metadata.get("capabilities"), str)
                            else metadata.get("capabilities", [])
                        )
                        if str(item)
                    }
                ),
            }
        )
        return metadata
