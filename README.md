# HiStrA Job Runner

A local, network-agnostic Windows worker for running prepared HiStrA Bridges
models and extracting small JSON result packages.

This is **step 1** of the distributed-worker architecture. It deliberately has
no HTTPS, token, server, queue, or GitHub update logic. A future network worker
only needs to download a job directory, call this package, and upload the
`output/` directory.

## Responsibility boundary

The **server/model-generation side** is responsible for:

- bridge geometry, materials, load positions, scour scenarios and HRX mutation;
- generating the final `.hrx` sent to the client;
- deciding which analyses and result subsets are required.

The **client runner** is responsible for:

- validating a self-contained job package;
- staging the HRX in an isolated attempt workspace;
- running the mesh and requested analyses;
- resolving analysis dependencies through `InitialAnalysisKey`;
- checking solver exit codes, HRX states and the results database;
- extracting only the requested rows into JSON;
- preserving logs, provenance and raw results until a later acknowledgement.

## Package layout

```text
histra-job-runner/
├── pyproject.toml
├── src/histra_runner/
│   ├── cli.py          # validate, run and local parallel batch commands
│   ├── config.py       # machine-specific TOML settings
│   ├── schema.py       # job.json schema and package validation
│   ├── hrx.py          # analysis selection, dependencies and completion evidence
│   ├── solver.py       # local/PsExec process execution and targeted timeout cleanup
│   ├── extraction.py   # read-only SQLite extraction without pandas
│   ├── runner.py       # job lifecycle and workspace orchestration
│   └── state.py        # durable state.json transitions
├── examples/
├── docs/
└── tests/
```

## Installation for development

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

The runtime package uses only the Python standard library. Python 3.11 or later
is required.

## Configure one computer

Copy `examples/runner.toml` and edit the solver path:

```toml
[solver]
executable = 'C:\Program Files\Gruppo Sismica\HiStrA Bridges 2025.1.6\SolverHistra.exe'
mode = "local"
process_name = "SolverHistra.exe"
close_without_ask = true

[runner]
workspace_root = './work'
keep_raw_on_success = true
keep_raw_on_failure = true
```

Use `mode = "psexec"` and set `psexec_executable` only when that mode is truly
needed. PsExec mode requires an elevated process.

## Create a local job package

```text
jobs/bridge-001/
├── job.json
└── model.hrx
```

The HRX is already prepared by the server/model-generation code. It must contain
the mesh analysis and all requested analyses.

Example:

```json
{
  "schema_version": "1.0",
  "job_id": "bridge-001-scour-050",
  "model": {
    "path": "model.hrx",
    "sha256": null
  },
  "mesh": {
    "enabled": true,
    "analysis_name": "StartMesh",
    "timeout_seconds": 900
  },
  "analyses": [
    {
      "name": "LiveLoad_1",
      "timeout_seconds": 3600,
      "outputs": {
        "displacements": {
          "enabled": true,
          "all_steps": true,
          "model_point_ids": [101, 205]
        },
        "reactions": {
          "enabled": true,
          "all_steps": true
        },
        "modal_contributions": {
          "enabled": false,
          "top_n": 3
        }
      }
    }
  ],
  "validation": {
    "require_completed_state": true,
    "require_results_database": true,
    "minimum_results_bytes": 1
  },
  "metadata": {
    "scenario_id": "bridge-001-scour-050"
  }
}
```

`model.sha256` may be omitted or set to a real 64-character SHA-256 digest. It
should be populated by the server in the network stage.

## Commands

Validate without executing HiStrA:

```powershell
histra-runner validate .\jobs\bridge-001\job.json
```

Validate the machine configuration too:

```powershell
histra-runner validate .\jobs\bridge-001\job.json --config .\runner.toml
```

Run one job:

```powershell
histra-runner run .\jobs\bridge-001\job.json --config .\runner.toml
```

Run multiple local jobs. Colleagues can use `--workers 1`; a dedicated machine
can use a higher value such as `--workers 4`:

```powershell
histra-runner run-batch .\jobs --config .\runner.toml --workers 4
```

## Attempt workspace

Each execution creates a unique directory:

```text
work/<job_id>/<attempt_id>/
├── state.json
├── input/
│   ├── job.json
│   └── model.hrx
├── run/
│   ├── model.hrx
│   └── model.Results
├── logs/
│   ├── 000-StartMesh.stdout.log
│   ├── 001-Vert.stdout.log
│   ├── 002-LiveLoad_1.stdout.log
│   └── ...
└── output/
    ├── results.json
    └── run.json
```

On failure, the workspace also contains `output/failure.json` and
`logs/traceback.log`. Raw files are retained by default so they can later be
deleted only after the server accepts the upload.

## Migration from the original scripts

| Original module | Job-runner replacement |
|---|---|
| `run_program.py` | `solver.py` |
| `run_scenario.py` | `runner.py` and durable attempt workspaces |
| `extract_results.py` | `extraction.py` using `sqlite3` directly |
| analysis-state helpers in `modelxml` | `hrx.py` |
| printed status and swallowed exceptions | typed exceptions, `state.json`, `failure.json` |
| hard-coded executable paths | machine-specific `runner.toml` |
| global `taskkill /IM SolverHistra.exe` | targeted process-tree termination by PID |

`build_scenarios.py`, material changes, load-position creation and scour HRX
mutation should remain on the server/model-generation side. They are
intentionally not part of this client package.

## Network step later

The future HTTPS layer should be a thin adapter:

1. claim/download a package into a local inbox;
2. call `JobRunner.run_job_file(...)`;
3. upload `output/results.json`, `output/run.json` and selected logs;
4. delete `run/` only after the server acknowledges the result.

No solver, HRX, extraction or job-lifecycle code should need to change for that
step.
