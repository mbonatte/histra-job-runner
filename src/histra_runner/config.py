from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
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

    def validate(self, *, require_solver_files: bool = True) -> None:
        self.solver.validate(require_files=require_solver_files)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if not self.workspace_root.is_dir():
            raise ConfigurationError(f"Workspace root is not a directory: {self.workspace_root}")


def _expand_path(value: str, base_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def load_runner_config(path: str | Path) -> RunnerConfig:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ConfigurationError(f"Runner configuration not found: {config_path}")

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    try:
        solver_data = data["solver"]
        runner_data = data.get("runner", {})
        mode = str(solver_data.get("mode", "local"))
        executable = _expand_path(str(solver_data["executable"]), config_path.parent)
        raw_psexec = solver_data.get("psexec_executable")
        psexec = (
            _expand_path(str(raw_psexec), config_path.parent)
            if raw_psexec not in {None, ""}
            else None
        )
        workspace_root = _expand_path(
            str(runner_data.get("workspace_root", "./work")), config_path.parent
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid runner configuration: {exc}") from exc

    return RunnerConfig(
        solver=SolverConfig(
            executable=executable,
            mode=mode,
            psexec_executable=psexec,
            process_name=str(solver_data.get("process_name", "SolverHistra.exe")),
            close_without_ask=bool(solver_data.get("close_without_ask", True)),
        ),
        workspace_root=workspace_root,
        keep_raw_on_success=bool(runner_data.get("keep_raw_on_success", True)),
        keep_raw_on_failure=bool(runner_data.get("keep_raw_on_failure", True)),
    )
