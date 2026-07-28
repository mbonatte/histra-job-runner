from __future__ import annotations

from ..config import RunnerConfig
from ..errors import ConfigurationError
from .csharp import CSharpBackend
from .protocol import SolverBackend
from .python import PythonBackend


def build_backend(config: RunnerConfig) -> SolverBackend:
    backend_type = config.backend.type.casefold()
    if backend_type == "csharp":
        if config.solver is None:
            raise ConfigurationError("[solver] is required for backend.type='csharp'.")
        return CSharpBackend(config.solver)
    if backend_type == "python":
        return PythonBackend(config.python)
    raise ConfigurationError(
        f"Unsupported backend.type={config.backend.type!r}; expected 'csharp' or 'python'."
    )
