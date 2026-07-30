import json

import httpx
import pytest

from histra_runner.errors import ServerError
from histra_runner.network import ServerClient


def claim_json():
    return {
        "job_id": "job-001",
        "attempt_id": "attempt-001",
        "job_sha256": "1" * 64,
        "hrx_sha256": "2" * 64,
        "lease_expires_at": "2026-07-30T13:00:00Z",
        "package_url": "https://server.test/package",
    }


def test_network_client_protocol():
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers, request.content))
        if request.url.path == "/runners/register":
            return httpx.Response(200, json={"runner_id": "runner-1"})
        if request.url.path == "/claims":
            return httpx.Response(200, json=claim_json())
        if request.url.path == "/package":
            return httpx.Response(200, content=b"zip")
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"lease_expires_at": "later"})
        if request.url.path.endswith("/results") or request.url.path.endswith("/failed"):
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="https://server.test", transport=transport)
    client = ServerClient("https://server.test", api_token="secret", client=http)
    runner = client.register(runner_id="runner-1", name="test", version="1")
    claim = client.claim(runner)
    assert client.download_package(claim, runner) == b"zip"
    assert client.heartbeat(claim, runner) == "later"
    assert client.submit_results(claim, {"x": 1})["status"] == "ok"
    assert client.submit_failure(claim, runner_id=runner, error=RuntimeError("boom"))["status"] == "ok"
    assert any(headers.get("authorization") == "Bearer secret" for _, _, headers, _ in seen)
    assert any(headers.get("x-runner-id") == "runner-1" for _, path, headers, _ in seen if path == "/package")


def test_no_claim_returns_none():
    http = httpx.Client(
        base_url="https://server.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )
    assert ServerClient("https://server.test", client=http).claim("runner") is None


def test_http_error_is_wrapped():
    http = httpx.Client(
        base_url="https://server.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down")),
    )
    with pytest.raises(ServerError, match="HTTP 503"):
        ServerClient("https://server.test", client=http).register(
            runner_id=None, name="x", version="1"
        )
