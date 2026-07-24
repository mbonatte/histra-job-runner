"""HiStrA local runner and optional HTTPS worker adapter."""

from .config import (
    NetworkWorkerConfig,
    RunnerConfig,
    ServerConfig,
    SolverConfig,
    WorkerConfig,
    load_network_worker_config,
    load_runner_config,
)
from .network import Claim, ServerClient
from .runner import JobRunner, RunOutcome
from .schema import JobSpec, load_job_spec
from .worker import NetworkWorker, ProcessResult

__all__ = [
    "Claim",
    "JobRunner",
    "JobSpec",
    "NetworkWorker",
    "NetworkWorkerConfig",
    "ProcessResult",
    "RunOutcome",
    "RunnerConfig",
    "ServerClient",
    "ServerConfig",
    "SolverConfig",
    "WorkerConfig",
    "load_job_spec",
    "load_network_worker_config",
    "load_runner_config",
]

__version__ = "0.4.0"
