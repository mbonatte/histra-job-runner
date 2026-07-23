# HTTPS worker contract implemented in 0.3.0

The network adapter is implemented by `histra_runner.worker.NetworkWorker` and
`histra_runner.network.ServerClient`. The local `JobRunner` remains unaware of
HTTP, queues and worker registration.

## Registration

```http
POST /api/v1/workers/register
```

The configured worker name is stable and registration is idempotent. The
server-provided worker ID is used for claims and worker heartbeats.

## Claim

```http
POST /api/v1/jobs/claim
Content-Type: application/json

{"worker_id": "..."}
```

HTTP 204 means no work. Claim requests are not automatically retried because a
lost response may have already created a lease.

## Download and validation

The worker downloads `package_url`, rejects unsafe ZIP paths and symbolic
links, limits compressed/uncompressed size, and validates:

- claim `job_id` equals package `job_id`;
- claim `attempt_id` equals package `attempt_id`;
- the model file exists;
- the model SHA-256 equals the server-provided digest.

## Execution and heartbeats

The adapter calls only the public runner API:

```python
outcome = JobRunner(runner_config).run_job_file(downloaded_job_json)
```

The heartbeat thread maps local states to server states:

- local validation, staging, mutation and solver execution → `running`;
- local extraction → `extracting`;
- result or failure submission → `uploading`.

Temporary heartbeat failures do not stop HiStrA. HTTP 404/409 marks a lease as
lost, but the local run is preserved.

## Result submission

The worker uploads:

- `output/results.json`;
- `output/run.json`;
- generated `validation.json`;
- a bounded combined solver log when logs exist.

An interrupted result upload is retried from the existing completed workspace;
HiStrA is never rerun only because acknowledgement was lost.

## Failure submission

Local `output/failure.json` is posted to the server failure URL with the local
exit code when available. If reporting fails due to connectivity, the spool
record remains recoverable.

## Local durable states

`record.json` uses the following main states:

```text
claimed
downloading
downloaded
running
completed_local
uploading
accepted
failure_local
failure_reported
network_pending
orphaned
```

Only `accepted`, `failure_reported` and `orphaned` are terminal locally.
