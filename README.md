# HiStrA Job Runner

A filesystem- and network-capable runner for deterministic HiStrA analysis jobs.

## Architecture

The runner now separates job lifecycle from solver implementation:

```text
JobRunner
├── package validation and staging
├── workspace/state/log lifecycle
├── backend-neutral request construction
└── result and manifest serialization

SolverBackend
└── CSharpBackend
    ├── HRX analysis selection and dependency ordering
    ├── foundation-interface mutations
    ├── SolverHistra.exe subprocess execution
    ├── HRX completion validation
    └── .Results extraction
```

The abstraction is deliberately backend-neutral so a future Python backend can be added without changing `JobRunner` or the external job/result contracts.

## Backend protocol

```python
class SolverBackend(Protocol):
    name: str

    def validate(self) -> None:
        ...

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        ...
```

The concrete C# implementation is available as:

```python
from histra_runner.backends import CSharpBackend

backend = CSharpBackend(config.solver)
runner = JobRunner(config, backend=backend)
```

Existing code remains valid:

```python
runner = JobRunner(config)
```

Tests and integrations that inject a low-level fake solver also remain valid:

```python
runner = JobRunner(config, solver=fake_solver)
```

## Installation

```console
python -m pip install -e .
```

For development:

```console
python -m pip install -e '.[dev]'
pytest
```

## Local execution

Configure `runner.toml`, then run:

```console
histra-runner run --config runner.toml path/to/job.json
```

The attempt workspace contains:

```text
input/       immutable staged inputs
run/         mutable HRX and SolverHistra results
logs/        solver stdout/stderr and failures
output/      results.json, run.json, or failure.json
state.json   durable attempt state
```

## Compatibility guarantees for this refactor

- Existing job schema `1.0` is unchanged.
- Existing `results.json` schema `1.0` is unchanged.
- C# execution remains subprocess-based through `SubprocessSolver`.
- Foundation-interface reset/apply behavior and dependency ordering remain in the C# path.
- Existing runner TOML files continue to use the `[solver]` section.
- Run manifests add a top-level `backend` field; existing fields are retained.

## Network worker

The existing HTTPS adapter remains available:

```console
histra-worker check --config examples/runner.toml
histra-worker register --config examples/runner.toml
histra-worker run --config examples/runner.toml --once
```

Claims are intentionally not retried automatically. Package downloads and result uploads use bounded retries, packages are safely extracted, and attempt identity and model hashes are validated before execution.

## Tests

```console
pytest
```

The suite covers backend delegation, the existing C# execution sequence, completed dependencies, scour reset/persistence, output extraction, schema validation, package integrity, HTTP behavior, and a mocked claim-download-run-upload cycle.
