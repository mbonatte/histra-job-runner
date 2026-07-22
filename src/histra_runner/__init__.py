"""Local HiStrA job runner.

The package deliberately has no HTTP or queue dependency. A future worker can
obtain a job package from any source and pass it to :class:`JobRunner`.
"""

from .config import RunnerConfig, SolverConfig, load_runner_config
from .runner import JobRunner, RunOutcome
from .schema import JobSpec, load_job_spec

__all__ = [
    "JobRunner",
    "JobSpec",
    "RunOutcome",
    "RunnerConfig",
    "SolverConfig",
    "load_job_spec",
    "load_runner_config",
]

__version__ = "0.2.0"
