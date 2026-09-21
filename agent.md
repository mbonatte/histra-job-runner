# HiStrA Job Runner Agent Operating Manual

Quick-start guide, architectural contracts, and operating rules for AI agents working in `histra-job-runner`.

---

## 1. Project Overview & Role

`histra-job-runner` is the continuous, autonomous execution worker for the distributed HiStrA structural analysis platform.

It owns:
1. **Job Claiming**: Polling `histra-job-server` and claiming jobs atomically (`POST /claims`).
2. **Package Verification**: Downloading package archives and verifying SHA-256 integrity against the server manifest.
3. **In-Process Solver Execution**: Translating canonical `JobSpec` workflows into `histra-python` numerical calls via `HiStrAPythonBackend`.
4. **Scour Interface Mutations**: Applying sequential interface material changes (e.g. `Soil` -> `Soil_removed`) while preserving committed state.
5. **Heartbeat Lifecycle**: Emitting periodic background heartbeats during nonlinear solves to maintain server lease validity.
6. **Result & Failure Reporting**: Formatting and uploading reaction curves, displacements, convergence histories, and failure envelopes.

---

## 2. Architecture & Key Modules

```text
src/histra_runner/
├── worker.py                  # Polling loop, retry backoff, continuous daemon
├── histra_python_backend.py   # Default in-process solver adapter for histra-python
├── backends.py                # ExecutionBackend protocol, CommandBackend, PythonBackend
├── executor.py                # Isolated attempt workspace & lifecycle manager
├── package.py                 # Package download, validation, and extraction
├── network.py                 # ServerClient HTTP communications (httpx)
├── contracts.py               # Pydantic data schemas (Claim, ResultsUpload, FailureUpload)
└── canonical.py               # Canonical JSON serialization & SHA-256 helpers
```

---

## 3. Non-Negotiable Invariants

### 1. In-Process Python Solver Default
- Numerical execution MUST run in-process through `HiStrAPythonBackend` by default.
- Subprocess or shell execution (`CommandBackend`) is strictly an override for testing or external solver binaries.

### 2. Multi-Stage Scour Progression & State Preservation
- Scour workflows follow sequential dependent steps: `Vert` -> `Scour_1` -> `Scour_2`.
- Concrete interface mutations MUST specify `preserve_committed_state=True` to retain internal equilibrium states from the previous analysis stage.
- Removed foundation soil must mutate to the appropriate material template (e.g. `material_key=147`).

### 3. Active Heartbeat Protection
- Analyses can take multiple minutes. The worker MUST dispatch periodic background heartbeats (default: 30s) to `/jobs/{id}/attempts/{id}/heartbeat`.
- Missing heartbeats cause the server to expire the lease and re-queue the job to another runner.

### 4. Fail-Safe Result & Failure Envelopes
- Never exit or abort without notifying the server.
- On nonconvergence, divergence, or equilibrium failure, upload a structured `FailureUpload` with solver logs, recent iterations, and traceback to `/jobs/{id}/attempts/{id}/failure`.
- On success, upload `ResultsUpload` with complete `execution_summary`, `reactions`, and `displacements`.

### 5. Output Integrity
- Reaction outputs must contain `Step`, `R1`, `R2`, `R3`.
- Displacement outputs must contain `IdElement`, `Step`, `Ux`, `Uy`, `Uz`.

---

## 4. Environment & Verification

### Python Environment
- Requires Python 3.11–3.14.
- Requires `histra-python` installed in the environment.

### Running Tests
```bash
# Ensure virtualenv bin is in PATH (CommandBackend requires "python" binary)
export PATH="/home/mauricio/coding/histra-python/.venv/bin:$PATH"

# Run full runner test suite (60+ tests)
pytest -ra

# Run backend-specific tests
pytest tests/test_histra_python_backend.py tests/test_backends.py

# Check test coverage (threshold: 88%)
pytest --cov=histra_runner --cov-report=term-missing
```

---

## 5. CLI & Execution Commands

```bash
# Continuous worker loop
histra-runner worker \
  --server-url https://histra.bonatte.cloud \
  --api-token "$HISTRA_API_TOKEN" \
  --runner-id "worker-01" \
  --work-root ./work

# Single-cycle test execution
histra-runner worker --once --keep-workspaces
```
