# Refactor source review

## Numerical boundary before the change

`JobRunner` directly handled HRX analysis selection, dependency ordering, scour mutation, `SolverHistra.exe` invocation, completion validation, SQLite extraction, and artifact serialization.

## Numerical boundary after the change

`CSharpBackend` handles the numerical workflow and returns `SolverJobResult`. `JobRunner` handles only package/workspace lifecycle, translation to backend-neutral requests, backend invocation, logging, and artifact serialization.

## Compatibility decisions

- Default construction still selects the C# subprocess backend.
- The legacy `solver=` injection remains available.
- Existing `[solver]` TOML configuration remains valid.
- Existing job and results schemas remain at version `1.0`.
- A custom backend is not forced through C# solver configuration validation.
