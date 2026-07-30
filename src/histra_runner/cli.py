from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from pathlib import Path

from .backends import CommandBackend, PythonBackend
from .contracts import Claim
from .executor import RunnerExecutor
from .network import ServerClient
from .package import validate_package
from .worker import Worker


def backend_from_args(args):
    if args.python_backend:
        return PythonBackend(args.python_backend)
    if args.command:
        return CommandBackend(shlex.split(args.command), timeout_seconds=args.timeout)
    raise SystemExit("one of --python-backend or --command is required")


def validate_command(args) -> int:
    contents = validate_package(args.package, args.destination)
    print(json.dumps(contents.manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def run_package_command(args) -> int:
    claim = Claim.model_validate_json(Path(args.claim).read_text(encoding="utf-8"))
    outcome = RunnerExecutor(backend_from_args(args)).execute_package(
        args.package,
        args.workspace,
        runner_id=args.runner_id,
        claim=claim,
    )
    print(json.dumps(outcome.envelope, indent=2, sort_keys=True))
    return 0


def worker_command(args) -> int:
    with ServerClient(args.server, api_token=args.api_token) as client:
        worker = Worker(
            client=client,
            executor=RunnerExecutor(backend_from_args(args)),
            work_root=Path(args.work_root),
            runner_name=args.name,
            runner_version="1.0.0",
            runner_id=args.runner_id,
            heartbeat_interval_seconds=args.heartbeat_interval,
            keep_workspaces=args.keep_workspaces,
        )
        worker.register()
        while True:
            processed = worker.run_once()
            if args.once:
                return 0
            if not processed:
                time.sleep(args.poll_interval)


def add_backend_arguments(parser):
    parser.add_argument("--python-backend")
    parser.add_argument("--command", help="quoted command with {hrx}, {job}, {workspace}, {output}")
    parser.add_argument("--timeout", type=float)


def build_parser():
    parser = argparse.ArgumentParser(prog="histra-runner")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("package")
    validate.add_argument("destination")
    validate.set_defaults(func=validate_command)

    run_package = sub.add_parser("run-package")
    run_package.add_argument("package")
    run_package.add_argument("claim")
    run_package.add_argument("workspace")
    run_package.add_argument("--runner-id", required=True)
    add_backend_arguments(run_package)
    run_package.set_defaults(func=run_package_command)

    worker = sub.add_parser("worker")
    worker.add_argument("--server", default=os.getenv("HISTRA_SERVER_URL", "http://localhost:8000"))
    worker.add_argument("--api-token", default=os.getenv("HISTRA_API_TOKEN"))
    worker.add_argument("--runner-id", default=os.getenv("HISTRA_RUNNER_ID"))
    worker.add_argument("--name", default=os.getenv("HISTRA_RUNNER_NAME", "runner"))
    worker.add_argument("--work-root", default=os.getenv("HISTRA_WORK_ROOT", "./work"))
    worker.add_argument("--poll-interval", type=float, default=5.0)
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
