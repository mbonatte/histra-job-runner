# HiStrA Job Runner

HiStrA Job Runner is the always-on worker for the canonical HiStrA distributed-analysis platform.

Version **1.1.0** installs and uses **histra-python by default**. No backend argument is required:

```text
poll server → claim JOB → validate package → execute HRX with histra-python
→ upload results/failure → poll again
```

The Runner retains the explicit `--python-backend` and `--command` options for development and compatibility, but they are overrides rather than normal configuration.

## Install

Python 3.11 or newer and Git are required. Installing Runner also installs the pinned, tested `histra-python` revision declared in `pyproject.toml`.

### Windows

```powershell
py -3.12 -m venv C:\HiStrA-Runner\.venv
C:\HiStrA-Runner\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install "git+https://github.com/mbonatte/histra-job-runner.git@v1.1.0"
```

Verify both packages:

```powershell
histra-runner --version
python -c "import histra; print(histra.__version__)"
```

For development from a checkout:

```powershell
git clone https://github.com/mbonatte/histra-job-runner.git C:\Repositories\histra-job-runner
python -m pip install -e "C:\Repositories\histra-job-runner[test]"
```

## Run continuously

Set four values once:

```powershell
$env:HISTRA_SERVER_URL = "https://histra.bonatte.cloud"
$env:HISTRA_API_TOKEN = "THE_SAME_TOKEN_CONFIGURED_ON_THE_SERVER"
$env:HISTRA_RUNNER_ID = "mauricio-pc"
$env:HISTRA_RUNNER_NAME = "Mauricio PC"
$env:HISTRA_WORK_ROOT = "C:\HiStrA-Runner\work"
```

Start the worker:

```powershell
histra-runner worker
```

That command remains active. When there is no work it waits five seconds and asks again. After a network/server error it waits fifteen seconds and retries. During an analysis it sends lease heartbeats every thirty seconds.

Useful options:

```powershell
histra-runner worker `
  --poll-interval 5 `
  --retry-interval 15 `
  --heartbeat-interval 30 `
  --keep-workspaces
```

Use `--once` only for a controlled single-cycle test:

```powershell
histra-runner worker --once --keep-workspaces
```

## Start automatically on Windows

Copy and edit [`scripts/run-worker.ps1`](scripts/run-worker.ps1), then register it as a Scheduled Task:

```powershell
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\HiStrA-Runner\run-worker.ps1"'

$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
  -TaskName "HiStrA Job Runner" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -RunLevel Highest `
  -Force

Start-ScheduledTask -TaskName "HiStrA Job Runner"
```

Inspect the log:

```powershell
Get-Content C:\HiStrA-Runner\logs\runner.log -Tail 100 -Wait
```

## Canonical Python workflow

A minimal JOB workflow is:

```json
{
  "workflow": {
    "analyses": [
      {"name": "Vert"}
    ]
  }
}
```

The analysis name must match the analysis defined in the HRX. The Runner asks histra-python to execute dependency analyses in the order resolved from the HRX.

Default output behavior is:

- all reaction steps;
- all supported model-point displacement steps;
- no modal output.

An explicit workflow is:

```json
{
  "workflow": {
    "timeout_seconds": 7200,
    "analysis_timeout_seconds": 3600,
    "combination_row": 1,
    "outputs": {
      "reactions": {
        "enabled": true,
        "all_steps": true
      },
      "displacements": {
        "enabled": true,
        "all_steps": true,
        "model_point_ids": [101, 102]
      },
      "modal_contributions": {
        "enabled": false
      }
    },
    "analyses": [
      {"name": "Gravity"},
      {"name": "Scour-25", "timeout_seconds": 5400}
    ]
  }
}
```

Each output can instead select one committed step:

```json
{
  "enabled": true,
  "all_steps": false,
  "step": 20
}
```

### Interface-material mutations

Concrete mutations can be assigned before a named analysis:

```json
{
  "workflow": {
    "analyses": [
      {"name": "Gravity"},
      {"name": "Scour-25"}
    ],
    "interface_mutations": {
      "Scour-25": [
        {
          "interface_keys": [301, 302, 303],
          "material_key": 18,
          "preserve_committed_state": true
        }
      ]
    }
  }
}
```

The same `interface_mutations` field can be placed directly inside an analysis item. Translation from bridge-level terms such as scour depth/location to concrete interface keys belongs to JOB creation/scenario generation; the Runner does not guess engineering semantics.

See [`docs/JOB_WORKFLOW.md`](docs/JOB_WORKFLOW.md) for the validation rules and result structure.

## Result structure

The uploaded `results` object contains projected outputs by requested analysis:

```json
{
  "backend": "histra-python",
  "analyses": {
    "Vert": {
      "execution": {
        "analysis_key": 7,
        "analysis_name": "Vert",
        "exit_code": 0,
        "outcome": "completed",
        "completed": true,
        "runtime_seconds": 12.4,
        "step_count": 41,
        "committed_step_count": 40
      },
      "outputs": {
        "reactions": [],
        "displacements": []
      },
      "mutations": []
    }
  }
}
```

The `run` object records the Runner backend, exact histra-python version, analysis order, execution summaries, elapsed time and solver metadata. With `--keep-workspaces`, `results.json`, `run.json`, `solver.log`, and `result-envelope.json` remain under the work directory.

## Backend overrides

Use only when deliberately testing another trusted adapter:

```powershell
histra-runner worker --python-backend "package.module:execute"
```

or:

```powershell
histra-runner worker --command 'python adapter.py --hrx {hrx} --job {job} --output {output}'
```

A command adapter must create `results.json` and `run.json` in `{output}`.

## Numerical capability boundary

The default backend follows the capabilities of the installed histra-python revision. Unsupported HRX features are rejected during solver capability preflight and reported to the Server as a failed attempt rather than silently ignored.

## Tests

```bash
python -m pip install -e ".[test]"
pytest --cov=histra_runner --cov-report=term-missing
```

Optional numerical release gate:

```bash
HISTRA_REAL_HRX=/path/model.hrx \
HISTRA_REAL_ANALYSIS=Vert \
pytest tests/test_real_histra_optional.py
```
