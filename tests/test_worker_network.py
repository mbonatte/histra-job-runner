from __future__ import annotations

from pathlib import Path
import hashlib
import json
import zipfile
from io import BytesIO

import httpx

from histra_runner.config import (
    NetworkWorkerConfig,
    RunnerConfig,
    ServerConfig,
    SolverConfig,
    WorkerConfig,
)
from histra_runner.jsonio import write_json_atomic
from histra_runner.network import ServerClient
from histra_runner.runner import RunOutcome
from histra_runner.schema import load_job_spec
from histra_runner.worker import NetworkWorker


def _package_bytes() -> bytes:
    model = b"<root />"
    job = {
        "schema_version": "1.0",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "model": {
            "path": "model.hrx",
            "sha256": hashlib.sha256(model).hexdigest(),
        },
        "analyses": [{"name": "Analysis-1"}],
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("job.json", json.dumps(job))
        archive.writestr("model.hrx", model)
    return buffer.getvalue()


class FakeRunner:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def run_job_file(self, job_path: Path) -> RunOutcome:
        spec = load_job_spec(job_path)
        assert spec.attempt_id is not None
        workspace = self.workspace_root / spec.job_id / spec.attempt_id
        (workspace / "output").mkdir(parents=True)
        (workspace / "logs").mkdir()
        write_json_atomic(
            workspace / "state.json",
            {"state": "completed", "history": []},
        )
        results = workspace / "output" / "results.json"
        run = workspace / "output" / "run.json"
        write_json_atomic(
            results,
            {
                "schema_version": "1.0",
                "job_id": spec.job_id,
                "attempt_id": spec.attempt_id,
                "analyses": {"Analysis-1": {}},
            },
        )
        write_json_atomic(
            run,
            {
                "schema_version": "1.0",
                "job_id": spec.job_id,
                "attempt_id": spec.attempt_id,
                "status": "completed",
                "validation": [],
                "mutations": [],
            },
        )
        return RunOutcome(spec.job_id, spec.attempt_id, workspace, results, run)


def test_network_worker_downloads_runs_and_uploads(tmp_path: Path) -> None:
    package = _package_bytes()
    claimed = False
    uploaded = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal claimed, uploaded
        path = request.url.path
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ready", "version": "0.1.0"})
        if path == "/api/v1/workers/register":
            return httpx.Response(200, json={"id": "worker-1", "enabled": True})
        if path == "/api/v1/jobs/claim":
            if claimed:
                return httpx.Response(204)
            claimed = True
            return httpx.Response(
                200,
                json={
                    "job_id": "job-1",
                    "attempt_id": "attempt-1",
                    "lease_expires_at": "2026-01-01T00:00:00Z",
                    "package_url": "/package",
                    "heartbeat_url": "/heartbeat",
                    "results_url": "/results",
                    "failure_url": "/failed",
                },
            )
        if path == "/package":
            return httpx.Response(200, content=package)
        if path == "/heartbeat":
            return httpx.Response(200, json={"status": "running"})
        if path == "/results":
            body = request.read()
            assert b'results.json' in body
            assert b'run.json' in body
            uploaded = True
            return httpx.Response(200, json={"status": "completed"})
        raise AssertionError(path)

    workspace_root = tmp_path / "work"
    config = NetworkWorkerConfig(
        runner=RunnerConfig(
            solver=SolverConfig(executable=tmp_path / "missing.exe"),
            workspace_root=workspace_root,
        ),
        server=ServerConfig(
            base_url="https://example.test",
            retry_backoff_seconds=0,
        ),
        worker=WorkerConfig(
            name="test-worker",
            max_parallel_jobs=1,
            spool_root=tmp_path / "spool",
            heartbeat_seconds=0.01,
        ),
    )
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    server_client = ServerClient(config.server, client=http_client)
    with NetworkWorker(
        config,
        client=server_client,
        runner_factory=lambda: FakeRunner(workspace_root),
    ) as worker:
        results = worker.run_once()
    assert uploaded is True
    assert len(results) == 1
    assert results[0].local_status == "accepted"
    record = json.loads(
        (tmp_path / "spool" / "job-1" / "attempt-1" / "record.json").read_text()
    )
    assert record["status"] == "accepted"
