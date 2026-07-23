from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class HistraRunnerError(Exception):
    """Base exception for predictable runner failures."""


class ConfigurationError(HistraRunnerError):
    pass


class JobValidationError(HistraRunnerError):
    pass


class HrxValidationError(HistraRunnerError):
    pass


class ResultExtractionError(HistraRunnerError):
    pass


class NetworkWorkerError(HistraRunnerError):
    pass


class ServerRequestError(NetworkWorkerError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class LeaseLostError(ServerRequestError):
    """The server no longer considers an attempt active/current."""


class PackageError(NetworkWorkerError):
    pass


@dataclass(frozen=True)
class FailedExecution:
    model_path: Path
    command: tuple[str, ...]
    return_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class SolverExecutionError(HistraRunnerError):
    def __init__(self, message: str, execution: FailedExecution):
        super().__init__(message)
        self.execution = execution


class JobRunError(HistraRunnerError):
    """Wraps a failed job and points to its preserved workspace."""

    def __init__(self, job_id: str, workspace: Path, cause: Exception):
        super().__init__(f"Job '{job_id}' failed in {workspace}: {cause}")
        self.job_id = job_id
        self.workspace = workspace
        self.cause = cause
