from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from .config import load_network_worker_config
from .errors import HistraRunnerError
from .worker import NetworkWorker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="histra-worker",
        description="Pull and execute HiStrA jobs from a HiStrA job server.",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "Check server readiness and validate local configuration."),
        ("register", "Register or refresh this worker on the server."),
        ("run", "Run the network worker."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, required=True)
        if name == "run":
            command.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_network_worker_config(args.config)
        with NetworkWorker(config) as worker:
            if args.command == "check":
                print(json.dumps(worker.check_server(), indent=2))
                print(f"CONFIG VALID: {args.config.resolve()}")
                return 0
            if args.command == "register":
                print(json.dumps(worker.register(), indent=2, default=str))
                return 0
            if args.command == "run":
                if args.once:
                    results = worker.run_once()
                    if not results:
                        print("NO JOBS")
                        return 0
                    for result in results:
                        print(
                            f"{result.local_status.upper()}: "
                            f"{result.job_id}/{result.attempt_id} -> {result.detail}"
                        )
                    return int(any(r.local_status != "accepted" for r in results))
                worker.run_forever()
                return 0
    except (HistraRunnerError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
