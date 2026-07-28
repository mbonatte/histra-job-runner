# Network adapter contract

The HTTPS worker remains an adapter around `JobRunner`. It registers capacity, claims an attempt, downloads and validates the package, invokes the local runner, sends heartbeats, and uploads normalized artifacts.

The backend refactor does not change the job-server endpoints or package identity rules. The selected backend is recorded in `state.json` and `run.json`, so server-side diagnostics can distinguish implementations later without changing the upload shape.
