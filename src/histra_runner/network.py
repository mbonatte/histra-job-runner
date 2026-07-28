from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
import json
import logging
import time

import httpx

from .config import ServerConfig
from .errors import LeaseLostError, ServerRequestError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt_id: str
    lease_expires_at: str
    package_url: str
    heartbeat_url: str
    results_url: str
    failure_url: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Claim":
        required = (
            "job_id",
            "attempt_id",
            "lease_expires_at",
            "package_url",
            "heartbeat_url",
            "results_url",
            "failure_url",
        )
        missing = [key for key in required if not isinstance(value.get(key), str)]
        if missing:
            raise ServerRequestError(
                f"Claim response is missing string field(s): {', '.join(missing)}"
            )
        return cls(**{key: value[key] for key in required})

    def as_dict(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "lease_expires_at": self.lease_expires_at,
            "package_url": self.package_url,
            "heartbeat_url": self.heartbeat_url,
            "results_url": self.results_url,
            "failure_url": self.failure_url,
        }


class ServerClient:
    """Small synchronous client for the HiStrA job-server HTTP contract."""

    def __init__(
        self,
        config: ServerConfig,
        *,
        client: httpx.Client | None = None,
        user_agent: str = "histra-job-runner/0.4.0",
    ):
        self.config = config
        self.config.validate()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            verify=config.verify_tls,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ServerClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(f"{self.config.base_url.rstrip('/')}/", path_or_url.lstrip("/"))

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict) and "detail" in payload:
                return str(payload["detail"])
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return response.text[:2000]

    def _raise_for_response(
        self, response: httpx.Response, *, lease_sensitive: bool = False
    ) -> None:
        if 200 <= response.status_code < 300:
            return
        detail = self._response_detail(response)
        message = f"Server returned HTTP {response.status_code}: {detail}"
        error_type = (
            LeaseLostError
            if lease_sensitive and response.status_code in {404, 409, 410}
            else ServerRequestError
        )
        raise error_type(
            message,
            status_code=response.status_code,
            response_text=response.text,
        )

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        retry: bool,
        lease_sensitive: bool = False,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        url = self.resolve_url(path_or_url)
        attempts = self.config.retry_attempts if retry else 1
        last_error: Exception | None = None
        for index in range(attempts):
            try:
                response = self._client.request(
                    method,
                    url,
                    timeout=timeout or self.config.request_timeout_seconds,
                    **kwargs,
                )
                if (
                    retry
                    and response.status_code in {429, 502, 503, 504}
                    and index + 1 < attempts
                ):
                    delay = self.config.retry_backoff_seconds * (2**index)
                    if delay:
                        time.sleep(delay)
                    continue
                self._raise_for_response(response, lease_sensitive=lease_sensitive)
                return response
            except httpx.RequestError as exc:
                last_error = exc
                if index + 1 >= attempts:
                    break
                delay = self.config.retry_backoff_seconds * (2**index)
                if delay:
                    time.sleep(delay)
        raise ServerRequestError(f"Could not reach {url}: {last_error}") from last_error

    def ready(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready", retry=True).json()

    def register_worker(
        self,
        *,
        name: str,
        max_parallel_jobs: int,
        worker_version: str,
        solver_version: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/workers/register",
            json={
                "name": name,
                "max_parallel_jobs": max_parallel_jobs,
                "worker_version": worker_version,
                "solver_version": solver_version,
                "metadata": metadata,
            },
            retry=True,
        ).json()

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        max_parallel_jobs: int,
        worker_version: str,
        solver_version: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/workers/{worker_id}/heartbeat",
            json={
                "max_parallel_jobs": max_parallel_jobs,
                "worker_version": worker_version,
                "solver_version": solver_version,
                "metadata": metadata,
            },
            retry=False,
        ).json()

    def claim(self, worker_id: str) -> Claim | None:
        # Intentionally not retried: a lost response may already have leased a job.
        response = self._request(
            "POST",
            "/api/v1/jobs/claim",
            json={"worker_id": worker_id},
            retry=False,
        )
        if response.status_code == 204:
            return None
        return Claim.from_dict(response.json())

    def attempt_heartbeat(
        self,
        claim: Claim,
        *,
        status: str,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            claim.heartbeat_url,
            json={"status": status, "progress": progress or {}},
            retry=False,
            lease_sensitive=True,
        ).json()

    def download_package(self, claim: Claim, destination: Path) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        url = self.resolve_url(claim.package_url)
        last_error: Exception | None = None
        for index in range(self.config.retry_attempts):
            temporary.unlink(missing_ok=True)
            try:
                with self._client.stream(
                    "GET", url, timeout=self.config.download_timeout_seconds
                ) as response:
                    self._raise_for_response(response, lease_sensitive=True)
                    announced = response.headers.get("content-length")
                    if announced and int(announced) > self.config.maximum_package_bytes:
                        raise ServerRequestError(
                            "Job package exceeds configured maximum_package_bytes."
                        )
                    size = 0
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.config.maximum_package_bytes:
                                raise ServerRequestError(
                                    "Job package exceeds configured maximum_package_bytes."
                                )
                            handle.write(chunk)
                temporary.replace(destination)
                return destination
            except LeaseLostError:
                temporary.unlink(missing_ok=True)
                raise
            except (httpx.RequestError, OSError, ValueError) as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
                if index + 1 >= self.config.retry_attempts:
                    break
                delay = self.config.retry_backoff_seconds * (2**index)
                if delay:
                    time.sleep(delay)
        raise ServerRequestError(
            f"Could not download package from {url}: {last_error}"
        ) from last_error

    def upload_results(
        self,
        claim: Claim,
        *,
        results_path: Path,
        run_path: Path,
        validation_path: Path | None = None,
        solver_log_path: Path | None = None,
        extractor_log_path: Path | None = None,
    ) -> dict[str, Any]:
        files: dict[str, tuple[str, bytes, str]] = {
            "results_file": ("results.json", results_path.read_bytes(), "application/json"),
            "run_file": ("run.json", run_path.read_bytes(), "application/json"),
        }
        optional = (
            ("validation_file", validation_path, "validation.json", "application/json"),
            ("solver_log", solver_log_path, "solver.log", "text/plain"),
            ("extractor_log", extractor_log_path, "extractor.log", "text/plain"),
        )
        for field, path, filename, content_type in optional:
            if path is not None and path.is_file():
                files[field] = (filename, path.read_bytes(), content_type)
        return self._request(
            "POST",
            claim.results_url,
            files=files,
            retry=True,
            lease_sensitive=True,
            timeout=self.config.upload_timeout_seconds,
        ).json()

    def report_failure(
        self,
        claim: Claim,
        *,
        reason: str,
        retryable: bool,
        exit_code: int | None,
        run: dict[str, Any] | None,
        validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            claim.failure_url,
            json={
                "reason": reason[:10_000],
                "retryable": retryable,
                "exit_code": exit_code,
                "run": run,
                "validation": validation,
            },
            retry=True,
            lease_sensitive=True,
        ).json()
