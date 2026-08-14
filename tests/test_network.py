import httpx
import pytest

from histra_runner.contracts import Claim
from histra_runner.errors import ServerError
from histra_runner.network import ServerClient


CLAIM = {
    "job_id": "j",
    "attempt_id": "a",
    "job_sha256": "1" * 64,
    "hrx_sha256": "2" * 64,
    "lease_expires_at": "later",
    "package_url": "/package",
}


def test_network_protocol_and_auth():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/runners/register":
            return httpx.Response(200, json={"runner_id": "r"})
        if request.url.path == "/claims":
            return httpx.Response(200, json=CLAIM)
        if request.url.path == "/package":
            return httpx.Response(200, content=b"zip")
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"lease_expires_at": "later2"})
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(base_url="https://server", transport=httpx.MockTransport(handler))
    api = ServerClient("https://server", api_token="secret", client=client)
    assert api.register(runner_id=None, name="n", version="1", capabilities={}) == "r"
    claim = api.claim("r")
    assert isinstance(claim, Claim)
    assert api.download_package(claim, "r") == b"zip"
    assert api.heartbeat(claim, "r") == "later2"
    assert api.submit_results(claim, {"ok": True}) == {"ok": True}
    assert api.submit_failure(claim, runner_id="r", error=ValueError("bad")) == {"ok": True}
    assert all(r.headers["authorization"] == "Bearer secret" for r in requests)


def test_claim_204_and_http_error():
    client = httpx.Client(
        base_url="https://server",
        transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
    )
    assert ServerClient("https://server", client=client).claim("r") is None
    error_client = httpx.Client(
        base_url="https://server",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, text="no")),
    )
    with pytest.raises(ServerError, match="HTTP 401"):
        ServerClient("https://server", client=error_client).register(
            runner_id=None, name="n", version="1"
        )
