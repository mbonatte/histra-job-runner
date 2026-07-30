# histra-job-runner

The Runner is deliberately ignorant of model generation and queue internals. It
accepts a leased package, verifies it, invokes a configured analysis adapter,
and returns results with the exact JOB and HRX provenance.

```text
Server package
  manifest.json
  job.json
  model.hrx
       |
       v
safe extraction + identity/hash validation
       |
       v
configured backend adapter
       |
       v
results + run metadata + logs
       |
       v
Server result endpoint
```

## Security boundary

Before any solver process starts, the Runner:

- rejects absolute paths, traversal, backslashes, symbolic links, duplicates,
  excess files, and oversized expanded packages;
- requires exactly `manifest.json`, canonical `job.json`, and the declared HRX;
- validates package protocol `1.0`;
- validates JOB ID, attempt ID, canonical JOB SHA-256, HRX size, and HRX SHA-256;
- cross-checks Builder provenance against the package manifest;
- binds all identities and digests to the active server claim.

The extracted JOB and HRX are then trusted only for that attempt.

## Analysis adapters

The generic Runner cannot embed proprietary HiStrA solver behavior. Instead it
has one stable adapter interface and two implementations.

### Command adapter

Use an argument vector with these placeholders:

- `{hrx}`: validated HRX path;
- `{job}`: validated canonical JOB path;
- `{workspace}`: extracted input directory;
- `{output}`: directory where the adapter must write outputs.

The adapter must create:

```text
output/results.json   # analysis result object
output/run.json       # solver/version/timing/provenance object
```

Example:

```bash
histra-runner worker \
  --server http://server:8000 \
  --runner-id workstation-01 \
  --name workstation-01 \
  --command 'python run_histra.py --model {hrx} --job {job} --output {output}'
```

This is where the existing HiStrA execution workflow should live. It can use the
`workflow` section without knowing how the HRX was created.

### Python adapter

A trusted package can expose:

```python
def execute(package, output_dir):
    return {
        "results": {...},
        "run": {...},
        "logs": "..."
    }
```

Run it with `--python-backend your_package.module:execute`.

## Worker protocol

The worker uses only the Server's unversioned 1.0 API:

1. register identity;
2. claim one JOB;
3. download its runner-bound ZIP;
4. heartbeat while the backend runs;
5. upload a provenance-bound result or a structured failure;
6. remove the workspace unless `--keep-workspaces` is enabled.

Run one claim for diagnostics:

```bash
histra-runner worker --once ...
```

Validate a package without executing it:

```bash
histra-runner validate package.zip ./inspection
```

## Tests

```bash
python -m pip install -e '.[test]'
pytest
```

The suite includes hostile ZIPs, tampered manifests and payloads, process and
Python adapters, network protocol behavior, heartbeat operation, cleanup, and
failure reporting. Real solver acceptance tests should be added in the private
adapter repository using licensed HiStrA fixtures.
