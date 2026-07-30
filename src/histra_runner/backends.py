from __future__ import annotations

import importlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .errors import BackendError
from .package import PackageContents


@dataclass(frozen=True)
class ExecutionResult:
    results: dict[str, Any]
    run: dict[str, Any]
    logs: str = ""


class ExecutionBackend(Protocol):
    def execute(self, package: PackageContents, output_dir: Path) -> ExecutionResult: ...


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackendError(f"backend did not create {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BackendError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BackendError(f"{label} must contain a JSON object")
    return value


class CommandBackend:
    """Execute an external solver adapter using an argument-vector template."""

    def __init__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
        environment: dict[str, str] | None = None,
    ):
        if not command:
            raise ValueError("command cannot be empty")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.environment = environment or {}

    def execute(self, package: PackageContents, output_dir: Path) -> ExecutionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        values = {
            "hrx": str(package.hrx_path),
            "job": str(package.job_path),
            "workspace": str(package.root),
            "output": str(output_dir),
        }
        try:
            command = [part.format_map(values) for part in self.command]
        except KeyError as exc:
            raise BackendError(f"unknown command placeholder: {exc}") from exc
        env = os.environ.copy()
        env.update(self.environment)
        try:
            completed = subprocess.run(
                command,
                cwd=package.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"backend process could not complete: {exc}") from exc
        logs = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}".strip()
        if completed.returncode != 0:
            raise BackendError(
                f"backend exited with code {completed.returncode}\n{logs}"
            )
        results = _load_json_object(output_dir / "results.json", "results")
        run = _load_json_object(output_dir / "run.json", "run metadata")
        run.setdefault("exit_code", completed.returncode)
        run.setdefault("command", command)
        return ExecutionResult(results=results, run=run, logs=logs)


class PythonBackend:
    """Load a trusted in-process adapter as `module:function`."""

    def __init__(self, target: str):
        self.target = target
        self._callable = self._resolve(target)

    @staticmethod
    def _resolve(target: str) -> Callable[[PackageContents, Path], Any]:
        try:
            module_name, function_name = target.split(":", 1)
            function = getattr(importlib.import_module(module_name), function_name)
        except (ValueError, ImportError, AttributeError) as exc:
            raise BackendError(f"cannot load Python backend {target!r}: {exc}") from exc
        if not callable(function):
            raise BackendError(f"Python backend {target!r} is not callable")
        return function

    def execute(self, package: PackageContents, output_dir: Path) -> ExecutionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            value = self._callable(package, output_dir)
        except Exception as exc:
            raise BackendError(f"Python backend failed: {exc}") from exc
        if isinstance(value, ExecutionResult):
            return value
        if isinstance(value, dict):
            try:
                return ExecutionResult(
                    results=value["results"],
                    run=value["run"],
                    logs=value.get("logs", ""),
                )
            except (KeyError, TypeError) as exc:
                raise BackendError("Python backend returned an invalid mapping") from exc
        raise BackendError("Python backend must return ExecutionResult or a result mapping")
