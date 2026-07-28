from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import time
from typing import Protocol

from .config import SolverConfig
from .errors import FailedExecution, SolverExecutionError
from .jsonio import utc_now_iso


@dataclass(frozen=True)
class SolverExecution:
    model_path: Path
    command: tuple[str, ...]
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int
    stdout: str
    stderr: str

    def as_dict(self) -> dict:
        return {
            "model_path": str(self.model_path),
            "command": list(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "return_code": self.return_code,
        }


class SolverClient(Protocol):
    def run(self, model_path: Path, timeout_seconds: float) -> SolverExecution: ...


def is_running_as_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class SubprocessSolver:
    def __init__(self, config: SolverConfig):
        self.config = config

    def build_command(self, model_path: Path) -> tuple[str, ...]:
        solver_args = [
            str(self.config.executable),
            "run",
            str(model_path),
            "-CloseWithoutAsk",
            str(self.config.close_without_ask).lower(),
        ]
        if self.config.mode == "local":
            return tuple(solver_args)
        if self.config.mode == "psexec":
            if not is_running_as_admin():
                raise SolverExecutionError(
                    "PsExec mode requires Administrator privileges.",
                    FailedExecution(model_path, tuple(solver_args), None, "", "", False, 0.0),
                )
            assert self.config.psexec_executable is not None
            return (
                str(self.config.psexec_executable),
                "-accepteula",
                "-nobanner",
                "-i",
                "1",
                "-h",
                *solver_args,
            )
        raise ValueError(f"Unsupported solver mode: {self.config.mode}")

    def run(self, model_path: Path, timeout_seconds: float) -> SolverExecution:
        model_path = model_path.resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"Model not found: {model_path}")
        command = self.build_command(model_path)
        started_at = utc_now_iso()
        started = time.perf_counter()
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()
            duration = time.perf_counter() - started
            failed = FailedExecution(
                model_path,
                command,
                process.returncode,
                (exc.stdout or "") + (stdout or ""),
                (exc.stderr or "") + (stderr or ""),
                True,
                duration,
            )
            raise SolverExecutionError(
                f"Solver timed out after {timeout_seconds:g} seconds for {model_path.name}.",
                failed,
            ) from exc
        duration = time.perf_counter() - started
        stdout, stderr = stdout or "", stderr or ""
        if process.returncode != 0:
            failed = FailedExecution(
                model_path, command, process.returncode, stdout, stderr, False, duration
            )
            detail = (
                "Solver crashed with StackOverflowException"
                if "StackOverflowException" in stderr
                else "Solver returned a non-zero exit code"
            )
            raise SolverExecutionError(
                f"{detail} ({process.returncode}) for {model_path.name}.", failed
            )
        return SolverExecution(
            model_path,
            command,
            started_at,
            utc_now_iso(),
            duration,
            process.returncode,
            stdout,
            stderr,
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            process.kill()
