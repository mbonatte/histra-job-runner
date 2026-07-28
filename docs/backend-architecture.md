# Backend architecture

`JobRunner` is now an orchestration shell. It validates and stages a package, creates backend-neutral inputs, invokes one backend, and persists the backend result.

`CSharpBackend` owns all details that are specific to `SolverHistra.exe`: XML state changes, analysis dependency ordering, foundation-interface mutation, process execution, completion evidence, SQLite extraction, and the result database artifact.

## Extension contract

A future backend must implement `SolverBackend`. It receives a private mutable model path and must return normalized `SolverJobResult` data. The backend may create private artifacts, but it must not write `results.json`, `run.json`, `state.json`, or lifecycle logs; those remain the runner's responsibility.

## Error boundary

Backend exceptions flow through `JobRunner`, which records `failure.json`, a traceback, and failed execution output when the exception is a `SolverExecutionError`. The caller receives `JobRunError` with the workspace path for diagnosis.


## Timeout compatibility

`JobRunner` passes the aggregate timeout budget for explicitly requested analyses and the optional mesh analysis. `CSharpBackend` adds the historical per-analysis allowance for any implicit HRX predecessor analyses it must execute, so introducing the backend boundary does not shorten existing dependency chains.
