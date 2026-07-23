# HiStrA Job Runner 0.3.0

Windows job runner and HTTPS pull worker for HiStrA Bridges analyses.

The package keeps the numerical runner independent from the network adapter:

```text
HiStrA job server
    ↓ claim + package
HTTPS worker adapter
    ↓ local job.json
JobRunner
    ↓
HiStrA solver → scour mutation between analyses → result extraction
    ↓
results.json + run.json + validation/logs
    ↓ upload
HiStrA job server
```

## Responsibility boundary

The server is responsible for creating jobs, generating the base HRX, defining
analysis order, scour values, requested outputs and validation policy.

The client is responsible for downloading one attempt package, running the
analysis sequence, applying foundation-interface scour mutation immediately
before the relevant analysis, extracting the requested results and uploading
the small result package.

The network adapter does not contain HRX-generation or scenario-generation
logic.

## Features

- local `histra-runner` commands remain available without a server;
- `histra-worker` registers the machine and pulls jobs over HTTPS;
- configurable capacity: one job on colleague computers, four or more on a
  dedicated computer;
- package checksum verification and safe ZIP extraction;
- attempt heartbeats while HiStrA is running;
- connection loss does not terminate the solver;
- completed local attempts are preserved and uploaded again after restart;
- failed attempts are reported through the server failure endpoint;
- result upload is retried without rerunning HiStrA;
- server attempt IDs are preserved end to end;
- raw output is deleted only when configured and only after server acceptance;
- the existing scour functions and names are unchanged, including
  `run_update_foundation_ifaces` and `update_foundation_interfaces`.

Authentication is intentionally not included yet.

## Requirements

- Windows;
- Python 3.11 or later;
- a working HiStrA `SolverHistra.exe` installation;
- network access to `https://histra.bonatte.cloud`.

## Install

From the wheel:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .\histra_job_runner-0.3.0-py3-none-any.whl
```

For development:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

## Configure a computer

Copy `examples/runner.toml` to a permanent location and edit the solver path,
worker name and capacity.

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

[server]
base_url = "https://histra.bonatte.cloud"
verify_tls = true
request_timeout_seconds = 30
download_timeout_seconds = 180
upload_timeout_seconds = 180
retry_attempts = 5
retry_backoff_seconds = 2
maximum_package_bytes = 104857600

[worker]
name = "mauricio-desktop"
max_parallel_jobs = 4
spool_root = './spool'
poll_seconds = 15
heartbeat_seconds = 60
worker_heartbeat_seconds = 60
cleanup_package_on_accept = true
cleanup_workspace_on_accept = false
# solver_version = "2025.1.6"

[worker.metadata]
location = "home"
```

Recommended capacities:

```toml
# colleague computer
max_parallel_jobs = 1

# your own computer
max_parallel_jobs = 4
```

`cleanup_workspace_on_accept = false` preserves the raw HiStrA output after the
server accepts the result. Change it to `true` only when automatic deletion is
desired.

## Check the live server

The server exposes readiness at `/health/ready`.

```powershell
histra-worker check --config .\runner.toml
```

Expected output resembles:

```json
{
  "status": "ready",
  "version": "0.1.0"
}
```

## Register the computer

```powershell
histra-worker register --config .\runner.toml
```

Registration is idempotent by worker name. The returned worker ID is stored in:

```text
spool/worker.json
```

## Run the worker

Process one polling batch and exit, which is useful for the first real test:

```powershell
histra-worker run --config .\runner.toml --once
```

Run continuously:

```powershell
histra-worker run --config .\runner.toml
```

Stop with `Ctrl+C`. The worker stops claiming new jobs and waits for currently
running analyses to finish.

## Network workflow

For each claimed attempt, the worker performs:

```text
POST /api/v1/jobs/claim
    ↓
GET attempt package
    ↓
verify ZIP paths, job_id, attempt_id and HRX SHA-256
    ↓
run JobRunner locally
    ↓ periodic heartbeat
POST /api/v1/jobs/{job_id}/attempts/{attempt_id}/heartbeat
    ↓
upload results.json, run.json, validation.json and combined solver log
    ↓
POST /api/v1/jobs/{job_id}/attempts/{attempt_id}/results
```

If the local runner fails, the worker posts to the attempt's `/failed`
endpoint. Retryability is retained in the local failure record.

The claim request is deliberately not automatically retried. If a claim
response is lost, retrying blindly could consume another server slot. Package
download, heartbeats, result upload and failure reporting are safe to retry.

## Recovery after interruption

Network state is stored separately from the numerical workspace:

```text
spool/<job_id>/<attempt_id>/
├── record.json
├── package.zip
├── package/
├── validation.json
└── solver.log
```

Numerical files remain under:

```text
work/<job_id>/<attempt_id>/
├── state.json
├── input/
├── run/
├── logs/
└── output/
```

At startup the worker scans non-terminal spool records before claiming new
jobs:

- `output/run.json` exists and is completed: upload it again without rerunning;
- `output/failure.json` exists: report it again;
- package exists but execution never started: resume the attempt if the lease
  is still active;
- an incomplete workspace remains after a process/computer crash: report the
  attempt as interrupted and allow the server retry policy to create a new
  attempt;
- server returns HTTP 409/404 for the attempt: mark the local record orphaned
  and preserve it for diagnosis.

## Local-only commands

Validate a prepared package:

```powershell
histra-runner validate .\jobs\bridge-001\job.json --config .\runner.toml
```

Run one local package without the server:

```powershell
histra-runner run .\jobs\bridge-001\job.json --config .\runner.toml
```

Run a local batch:

```powershell
histra-runner run-batch .\jobs --config .\runner.toml --workers 4
```

## Scour mutation

The server defines the per-analysis scour scenario in `job.json`; the client
applies it between analyses. The existing names and modes remain:

```json
{
  "name": "Scour_1",
  "interfaces": {
    "pier_1": {
      "left": 0.20,
      "upstream": 0.10
    },
    "pier_2": 0.30
  }
}
```

Supported modes are `uniform`, `left`, `right`, `upstream` and `downstream`.
The numeric shorthand remains uniform scour. An analysis without an
`interfaces` entry preserves the state left by the previous analysis.

## Tests

```powershell
pytest
```

The tests cover the local numerical runner, scour mutation, schema validation,
HTTP contract, non-retried claims, safe package extraction and a complete
mocked claim-download-run-upload cycle.
