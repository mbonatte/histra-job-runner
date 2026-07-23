from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import json
import zipfile

import httpx
import pytest

from histra_runner.config import ServerConfig
from histra_runner.errors import PackageError, ServerRequestError
from histra_runner.network import Claim, ServerClient
from histra_runner.spool import AttemptSpool


def _claim() -> Claim:
    return Claim(
        job_id="job-1",
        attempt_id="attempt-1",
        lease_expires_at="2026-01-01T00:00:00Z",
        package_url="/package",
        heartbeat_url="/heartbeat",
        results_url="/results",
        failure_url="/failed",
    )


def test_client_contract_and_claim_is_not_retried(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready", "version": "0.1.0"})
        if request.url.path == "/api/v1/workers/register":
            return httpx.Response(200, json={"id": "worker-1", "enabled": True})
        if request.url.path == "/api/v1/jobs/claim":
            return httpx.Response(204)
        raise AssertionError(request.url)

    config = ServerConfig(base_url="https://example.test", retry_backoff_seconds=0)
    client = ServerClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.ready()["status"] == "ready"
    assert client.register_worker(
        name="pc",
        max_parallel_jobs=1,
        worker_version="0.3.0",
        solver_version=None,
        metadata={},
    )["id"] == "worker-1"
    assert client.claim("worker-1") is None
    assert [request.url.path for request in requests].count("/api/v1/jobs/claim") == 1


def test_claim_network_failure_is_not_retried() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        raise httpx.ConnectError("offline", request=request)

    config = ServerConfig(
        base_url="https://example.test",
        retry_attempts=5,
        retry_backoff_seconds=0,
    )
    client = ServerClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServerRequestError):
        client.claim("worker-1")
    assert count == 1


def test_safe_package_extraction_and_hash_validation(tmp_path: Path) -> None:
    model = b"<root />"
    digest = hashlib.sha256(model).hexdigest()
    job = {
        "schema_version": "1.0",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "model": {"path": "model.hrx", "sha256": digest},
        "analyses": [{"name": "A"}],
    }
    spool = AttemptSpool(tmp_path / "spool")
    record = spool.create(
        _claim(), worker_id="worker-1", server_base_url="https://example.test"
    )
    with zipfile.ZipFile(record.package_zip, "w") as archive:
        archive.writestr("job.json", json.dumps(job))
        archive.writestr("model.hrx", model)
    extracted = spool.extract_package(record, maximum_bytes=1024 * 1024)
    assert extracted == record.job_path
    assert record.status == "downloaded"


def test_package_rejects_path_traversal(tmp_path: Path) -> None:
    spool = AttemptSpool(tmp_path / "spool")
    record = spool.create(
        _claim(), worker_id="worker-1", server_base_url="https://example.test"
    )
    with zipfile.ZipFile(record.package_zip, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with pytest.raises(PackageError, match="Unsafe path"):
        spool.extract_package(record, maximum_bytes=1024)
