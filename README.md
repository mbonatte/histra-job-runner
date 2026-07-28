# HiStrA Job Runner

HiStrA Job Runner stages deterministic analysis packages and delegates numerical
execution to a selectable solver backend.

## Backends

```text
JobRunner
├── CSharpBackend   SolverHistra.exe + mutable HRX/.Results workflow
└── PythonBackend   histra-python, in-process committed-state workflow
```

Both backends implement the same `SolverBackend.run_job(...)` protocol and emit
the existing `results.json` schema. The C# backend remains the default, so old
configuration files continue to work unchanged.

### C# backend

The C# backend preserves the existing procedure:

1. select one analysis by editing the HRX;
2. apply foundation-interface changes to the HRX;
3. start a new `SolverHistra.exe` process;
4. let HiStrA restore predecessor state from HRX + `.Results`;
5. validate the HRX execution state and extract SQLite outputs.

### Python backend

The Python backend loads the HRX once and keeps committed displacements and
constitutive state in one `histra.AnalysisSession`. It does not create or read a
`.Results` database and does not change analysis execution flags in the HRX.
Before each requested analysis, the existing Job Runner scour helper is executed
against an XML copy to resolve the same semantic pier/direction request into
concrete interface and material keys. Those keys are then applied in memory.

Supported Python outputs are:

- C#-compatible `DisplModelPoints` rows: `IdElement`, `ParentKey`, `Step`,
  `Ux`, `Uy`, `Uz`;
- C#-compatible `ReactionSumStates` rows: `Step`, `R1`, `R2`, `R3`.

Modal contribution requests and P-Delta analyses fail during capability
preflight because the Python solver does not yet provide equivalent subsystems.

## Installation

Install the two repositories as sibling checkouts:

```console
python -m pip install -e ../histra-python
python -m pip install -e .
```

Or install their wheels from each repository's `dist/` directory.

A Git-based optional dependency is also available after the updated
`histra-python` repository is published:

```console
python -m pip install '.[python-backend]'
```

For development:

```console
python -m pip install -e '.[dev]'
pytest
```

## Configuration

Existing C# configuration remains valid:

```toml
[solver]
executable = 'C:\Program Files\Gruppo Sismica\HiStrA Bridges 2025.1.6\SolverHistra.exe'
mode = "local"

[runner]
workspace_root = "./work"
```

Python execution needs no `[solver]` section:

```toml
[backend]
type = "python"

[python]
combination_row = 1

[runner]
workspace_root = "./work"
keep_raw_on_success = true
keep_raw_on_failure = true
```

Run either backend with the same command and job schema:

```console
histra-runner run --config runner.toml path/to/job.json
```

For the Python backend, an enabled `mesh` item is treated as in-process model
preparation; the C# `StartMesh` analysis is not executed.

## Workspace

```text
input/       immutable staged inputs
run/         backend working model and C# .Results when applicable
logs/        subprocess or in-process solver logs
output/      results.json, run.json, or failure.json
state.json   durable attempt state
```

## Concurrency and cancellation

The Python solver currently uses shared `ModelManager` runtime fields. Therefore
`histra-python` serializes active solves with a process-wide lock. Job Runner can
still process C# jobs concurrently; Python solves within one worker process run
one at a time. Deadlines are cooperative and checked during analysis setup,
load steps, Newton iterations, line-search iterations, ALS retries, and between
expensive solver operations.

## Tests

```console
pytest
```

The suite includes the legacy C# workflow, backend delegation, Python backend
selection, semantic scour resolution, and an optional real Job Runner →
`histra-python` integration test:

```console
HISTRA_PYTHON_REPO=../histra-python pytest
```
