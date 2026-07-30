from __future__ import annotations

from typing import Any

import httpx

from .contracts import Claim
from .errors import ServerError


class ServerClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ):
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout_seconds
        )
        if client is not None and headers:
            self.client.headers.update(headers)
        self.base_url = base_url.rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ServerClient":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _raise(response: httpx.Response, operation: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:2000]
            raise ServerError(
                f"server {operation} failed with HTTP {response.status_code}: {detail}"
            ) from exc

    def register(
        self,
        *,
        runner_id: str | None,
        name: str,
        version: str,
        capabilities: dict[str, Any] | None = None,
    ) -> str:
        response = self.client.post(
            "/runners/register",
            json={
                "runner_id": runner_id,
                "name": name,
                "version": version,
                "capabilities": capabilities or {},
            },
        )
        self._raise(response, "registration")
        return response.json()["runner_id"]

    def claim(self, runner_id: str) -> Claim | None:
        response = self.client.post("/claims", json={"runner_id": runner_id})
        if response.status_code == 204:
            return None
        self._raise(response, "claim")
        return Claim.model_validate(response.json())

    def download_package(self, claim: Claim, runner_id: str) -> bytes:
        response = self.client.get(
            claim.package_url,
            headers={"X-Runner-ID": runner_id},
        )
        self._raise(response, "package download")
        return response.content

    def heartbeat(self, claim: Claim, runner_id: str) -> str:
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/heartbeat",
            json={"runner_id": runner_id},
        )
        self._raise(response, "heartbeat")
        return response.json()["lease_expires_at"]

    def submit_results(self, claim: Claim, envelope: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/results",
            json=envelope,
        )
        self._raise(response, "result upload")
        return response.json()

    def submit_failure(
        self,
        claim: Claim,
        *,
        runner_id: str,
        error: Exception,
        logs: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/failed",
            json={
                "runner_id": runner_id,
                "error_type": type(error).__name__,
                "message": str(error)[:4000] or type(error).__name__,
                "details": details or {},
                "logs": logs,
            },
        )
        self._raise(response, "failure upload")
        return response.json()
