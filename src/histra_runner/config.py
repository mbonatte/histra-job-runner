from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import os
import platform
import tomllib

from .errors import ConfigurationError


@dataclass(frozen=True)
class SolverConfig:
    executable: Path
    mode: str = "local"
    psexec_executable: Path | None = None
    process_name: str = "SolverHistra.exe"
    close_without_ask: bool = True

    def validate(self, *, require_files: bool = True) -> None:
        if self.mode not in {"local", "psexec"}:
            raise ConfigurationError("solver.mode must be 'local' or 'psexec'.")
        if require_files and not self.executable.is_file():
            raise ConfigurationError(f"Solver executable not found: {self.executable}")
        if self.mode == "psexec":
            if self.psexec_executable is None:
                raise ConfigurationError("solver.psexec_executable is required in psexec mode.")
            if require_files and not self.psexec_executable.is_file():
                raise ConfigurationError(f"PsExec executable not found: {self.psexec_executable}")


@dataclass(frozen=True)
class RunnerConfig:
    solver: SolverConfig
    workspace_root: Path
    keep_raw_on_success: bool = True
    keep_raw_on_failure: bool = True

    def validate(
        self,
        *,
        require_solver_files: bool = True,
        validate_solver: bool = True,
    ) -> None:
        if validate_solver:
            self.solver.validate(require_files=require_solver_files)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if not self.workspace_root.is_dir():
            raise ConfigurationError(f"Workspace root is not a directory: {self.workspace_root}")


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    verify_tls: bool = True
    request_timeout_seconds: float = 30.0
    download_timeout_seconds: float = 180.0
    upload_timeout_seconds: float = 180.0
    retry_attempts: int = 5
    retry_backoff_seconds: float = 2.0
    maximum_package_bytes: int = 100 * 1024 * 1024

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("server.base_url must be an absolute http:// or https:// URL.")
        if parsed.query or parsed.fragment:
            raise ConfigurationError("server.base_url must not contain a query or fragment.")
        if min(self.request_timeout_seconds, self.download_timeout_seconds, self.upload_timeout_seconds) <= 0:
            raise ConfigurationError("Server timeouts must be positive.")
        if self.retry_attempts < 1:
            raise ConfigurationError("server.retry_attempts must be at least 1.")
        if self.retry_backoff_seconds < 0:
            raise ConfigurationError("server.retry_backoff_seconds cannot be negative.")
        if self.maximum_package_bytes < 1:
            raise ConfigurationError("server.maximum_package_bytes must be positive.")


@dataclass(frozen=True)
class WorkerConfig:
    name: str
    max_parallel_jobs: int
    spool_root: Path
    poll_seconds: float = 15.0
    heartbeat_seconds: float = 60.0
    worker_heartbeat_seconds: float = 60.0
    solver_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cleanup_package_on_accept: bool = True
    cleanup_workspace_on_accept: bool = False

    def validate(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("worker.name must not be empty.")
        if not 1 <= self.max_parallel_jobs <= 64:
            raise ConfigurationError("worker.max_parallel_jobs must be between 1 and 64.")
        if min(self.poll_seconds, self.heartbeat_seconds, self.worker_heartbeat_seconds) <= 0:
            raise ConfigurationError("Worker intervals must be positive.")
        if not isinstance(self.metadata, dict):
            raise ConfigurationError("worker.metadata must be a TOML table/object.")
        self.spool_root.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class NetworkWorkerConfig:
    runner: RunnerConfig
    server: ServerConfig
    worker: WorkerConfig

    def validate(self, *, require_solver_files: bool = True) -> None:
        self.runner.validate(require_solver_files=require_solver_files)
        self.server.validate()
        self.worker.validate()


def _expand_path(value: str, base_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    return (path if path.is_absolute() else base_dir / path).resolve()


def _read_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ConfigurationError(f"Runner configuration not found: {config_path}")
    try:
        with config_path.open("rb") as handle:
            return config_path, tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {config_path}: {exc}") from exc


def _runner_from_data(config_path: Path, data: dict[str, Any]) -> RunnerConfig:
    try:
        solver_data = data["solver"]
        runner_data = data.get("runner", {})
        raw_psexec = solver_data.get("psexec_executable")
        return RunnerConfig(
            solver=SolverConfig(
                executable=_expand_path(str(solver_data["executable"]), config_path.parent),
                mode=str(solver_data.get("mode", "local")),
                psexec_executable=(
                    _expand_path(str(raw_psexec), config_path.parent)
                    if raw_psexec not in {None, ""}
                    else None
                ),
                process_name=str(solver_data.get("process_name", "SolverHistra.exe")),
                close_without_ask=bool(solver_data.get("close_without_ask", True)),
            ),
            workspace_root=_expand_path(
                str(runner_data.get("workspace_root", "./work")), config_path.parent
            ),
            keep_raw_on_success=bool(runner_data.get("keep_raw_on_success", True)),
            keep_raw_on_failure=bool(runner_data.get("keep_raw_on_failure", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid runner configuration: {exc}") from exc


def load_runner_config(path: str | Path) -> RunnerConfig:
    config_path, data = _read_config(path)
    return _runner_from_data(config_path, data)


def load_network_worker_config(path: str | Path) -> NetworkWorkerConfig:
    config_path, data = _read_config(path)
    runner = _runner_from_data(config_path, data)
    try:
        server_data = data["server"]
        worker_data = data.get("worker", {})
        metadata = worker_data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("worker.metadata must be a table")
        default_name = platform.node().strip() or "histra-worker"
        solver_version_raw = worker_data.get("solver_version")
        return NetworkWorkerConfig(
            runner=runner,
            server=ServerConfig(
                base_url=str(server_data["base_url"]).rstrip("/"),
                verify_tls=bool(server_data.get("verify_tls", True)),
                request_timeout_seconds=float(server_data.get("request_timeout_seconds", 30.0)),
                download_timeout_seconds=float(server_data.get("download_timeout_seconds", 180.0)),
                upload_timeout_seconds=float(server_data.get("upload_timeout_seconds", 180.0)),
                retry_attempts=int(server_data.get("retry_attempts", 5)),
                retry_backoff_seconds=float(server_data.get("retry_backoff_seconds", 2.0)),
                maximum_package_bytes=int(server_data.get("maximum_package_bytes", 100 * 1024 * 1024)),
            ),
            worker=WorkerConfig(
                name=str(worker_data.get("name", default_name)).strip(),
                max_parallel_jobs=int(worker_data.get("max_parallel_jobs", 1)),
                spool_root=_expand_path(str(worker_data.get("spool_root", "./spool")), config_path.parent),
                poll_seconds=float(worker_data.get("poll_seconds", 15.0)),
                heartbeat_seconds=float(worker_data.get("heartbeat_seconds", 60.0)),
                worker_heartbeat_seconds=float(worker_data.get("worker_heartbeat_seconds", 60.0)),
                solver_version=(
                    str(solver_version_raw).strip()
                    if solver_version_raw not in {None, ""}
                    else None
                ),
                metadata=dict(metadata),
                cleanup_package_on_accept=bool(worker_data.get("cleanup_package_on_accept", True)),
                cleanup_workspace_on_accept=bool(worker_data.get("cleanup_workspace_on_accept", False)),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid network worker configuration: {exc}") from exc
