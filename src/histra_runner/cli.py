from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

from .config import load_runner_config
from .errors import HistraRunnerError
from .hrx import validate_requested_analyses
from .runner import JobRunner
from .schema import load_job_spec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="histra-runner",
        description="Run self-contained HiStrA job packages locally.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a job package without running HiStrA.")
    validate.add_argument("job", type=Path)
    validate.add_argument("--config", type=Path, required=False)
    validate.add_argument("--skip-hash", action="store_true")

    run = subparsers.add_parser("run", help="Run one job package.")
    run.add_argument("job", type=Path)
    run.add_argument("--config", type=Path, required=True)

    batch = subparsers.add_parser("run-batch", help="Run all job.json files under a directory.")
    batch.add_argument("jobs_directory", type=Path)
    batch.add_argument("--config", type=Path, required=True)
    batch.add_argument("--workers", type=int, default=1)

    return parser


def _validate_job(job_path: Path, *, skip_hash: bool) -> None:
    resolved = job_path.resolve()
    spec = load_job_spec(resolved)
    model = spec.validate_package(resolved.parent, verify_hash=not skip_hash)
    names = [item.name for item in spec.analyses]
    if spec.mesh.enabled:
        names.append(spec.mesh.analysis_name)
    validate_requested_analyses(model, names)
    print(f"VALID: {spec.job_id} ({model})")


def _run_one(job_path: Path, config_path: Path) -> tuple[Path, bool, str]:
    try:
        config = load_runner_config(config_path)
        outcome = JobRunner(config).run_job_file(job_path)
        return job_path, True, str(outcome.workspace)
    except Exception as exc:
        return job_path, False, str(exc)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            _validate_job(args.job, skip_hash=args.skip_hash)
            if args.config:
                config = load_runner_config(args.config)
                config.validate(require_solver_files=True)
                print(f"CONFIG VALID: {args.config.resolve()}")
            return 0

        if args.command == "run":
            config = load_runner_config(args.config)
            outcome = JobRunner(config).run_job_file(args.job)
            print(f"COMPLETED: {outcome.job_id}/{outcome.attempt_id}")
            print(f"Workspace: {outcome.workspace}")
            print(f"Results:   {outcome.results_path}")
            return 0

        if args.command == "run-batch":
            if args.workers < 1:
                raise ValueError("--workers must be at least 1.")
            jobs_root = args.jobs_directory.resolve()
            jobs = sorted(jobs_root.rglob("job.json"))
            if not jobs:
                print(f"No job.json files found under {jobs_root}.", file=sys.stderr)
                return 2
            failures = 0
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_map = {
                    executor.submit(_run_one, job, args.config): job for job in jobs
                }
                for future in as_completed(future_map):
                    job, ok, detail = future.result()
                    label = "COMPLETED" if ok else "FAILED"
                    print(f"{label}: {job} -> {detail}")
                    failures += int(not ok)
            return 1 if failures else 0

    except (HistraRunnerError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2
