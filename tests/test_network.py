from __future__ import annotations

import httpx
import pytest

from histra_runner.config import ServerConfig
from histra_runner.errors import ServerRequestError
from histra_runner.network import ServerClient


def test_ready_register_and_claim_use_server_api():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"ready": True})
        if request.url.path.endswith("/workers/register"):
            return httpx.Response(200, json={"id": "worker-1"})
        if request.url.path.endswith("/jobs/claim"):
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    server = ServerClient(ServerConfig("https://example.test", retry_backoff_seconds=0), client=client)

    assert server.ready() == {"ready": True}
    assert server.register_worker(
        name="w", max_parallel_jobs=1, worker_version="1", solver_version=None, metadata={}
    )["id"] == "worker-1"
    assert server.claim("worker-1") is None
    assert seen == [
        ("GET", "/health/ready"),
        ("POST", "/api/v1/workers/register"),
        ("POST", "/api/v1/jobs/claim"),
    ]


def test_claim_is_not_retried_after_network_error():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    server = ServerClient(
        ServerConfig("https://example.test", retry_attempts=5, retry_backoff_seconds=0),
        client=client,
    )
    with pytest.raises(ServerRequestError):
        server.claim("worker-1")
    assert calls == 1
