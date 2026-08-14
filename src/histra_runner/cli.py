from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from . import __version__
from .backends import CommandBackend, PythonBackend
from .contracts import Claim
from .executor import RunnerExecutor
from .histra_python_backend import HiStrAPythonBackend
from .network import ServerClient
from .package import validate_package
from .worker import Worker


def backend_from_args(args: argparse.Namespace):
    if args.python_backend:
        return PythonBackend(args.python_backend)
    if args.command:
        return CommandBackend(shlex.split(args.command), timeout_seconds=args.timeout)
    return HiStrAPythonBackend(default_timeout_seconds=args.timeout or 3600.0)


def validate_command(args: argparse.Namespace) -> int:
    contents = validate_package(args.package, args.destination)
    print(json.dumps(contents.manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def run_package_command(args: argparse.Namespace) -> int:
    claim = Claim.model_validate_json(Path(args.claim).read_text(encoding="utf-8"))
    outcome = RunnerExecutor(backend_from_args(args)).execute_package(
        args.package,
        args.workspace,
        runner_id=args.runner_id,
        claim=claim,
    )
    print(json.dumps(outcome.envelope, indent=2, sort_keys=True))
    return 0


def worker_command(args: argparse.Namespace) -> int:
    backend = backend_from_args(args)
    capabilities = dict(getattr(backend, "capabilities", {}))
    with ServerClient(args.server, api_token=args.api_token) as client:
        worker = Worker(
            client=client,
            executor=RunnerExecutor(backend),
            work_root=Path(args.work_root),
            runner_name=args.name,
            runner_version=__version__,
            runner_id=args.runner_id,
            capabilities=capabilities,
            heartbeat_interval_seconds=args.heartbeat_interval,
            keep_workspaces=args.keep_workspaces,
        )
        while True:
            try:
                if worker.runner_id is None:
                    worker.register()
                processed = worker.run_once()
            except KeyboardInterrupt:
                return 130
            except Exception as exc:
                if args.once:
                    raise
                print(
                    f"Runner cycle failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(args.retry_interval)
                continue
            if args.once:
                return 0
            if not processed:
                time.sleep(args.poll_interval)


def add_backend_arguments(parser: argparse.ArgumentParser) -> None:
    overrides = parser.add_mutually_exclusive_group()
    overrides.add_argument(
        "--python-backend",
        help="override the built-in solver with a trusted module:function adapter",
    )
    overrides.add_argument(
        "--command",
        help="override the built-in solver with a quoted command using {hrx}, {job}, {workspace}, {output}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="default solver timeout in seconds (default: 3600)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="histra-runner",
        description="Continuously execute canonical HiStrA JOBs using histra-python.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    validate = sub.add_parser("validate", help="validate and extract a package")
    validate.add_argument("package")
    validate.add_argument("destination")
    validate.set_defaults(func=validate_command)

    run_package = sub.add_parser("run-package", help="execute one downloaded package")
    run_package.add_argument("package")
    run_package.add_argument("claim")
    run_package.add_argument("workspace")
    run_package.add_argument("--runner-id", required=True)
    add_backend_arguments(run_package)
    run_package.set_defaults(func=run_package_command)

    worker = sub.add_parser("worker", help="poll the server and execute jobs continuously")
    worker.add_argument(
        "--server",
        default=os.getenv("HISTRA_SERVER_URL", "http://localhost:8000"),
    )
    worker.add_argument("--api-token", default=os.getenv("HISTRA_API_TOKEN"))
    worker.add_argument("--runner-id", default=os.getenv("HISTRA_RUNNER_ID"))
    worker.add_argument("--name", default=os.getenv("HISTRA_RUNNER_NAME", "runner"))
    worker.add_argument("--work-root", default=os.getenv("HISTRA_WORK_ROOT", "./work"))
    worker.add_argument("--poll-interval", type=float, default=5.0)
    worker.add_argument("--retry-interval", type=float, default=15.0)
    worker.add_argument("--heartbeat-interval", type=float, default=30.0)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--keep-workspaces", action="store_true")
    add_backend_arguments(worker)
    worker.set_defaults(func=worker_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
