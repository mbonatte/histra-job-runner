# Changelog

## 0.4.0

- Add the backend-neutral `SolverBackend` protocol and immutable request/result types.
- Add `CSharpBackend`, preserving the existing `SolverHistra.exe` subprocess workflow.
- Refactor `JobRunner` so it owns workspace/package lifecycle while the backend owns numerical execution, HRX mutation, validation, and result extraction.
- Preserve the legacy `JobRunner(config, solver=...)` injection path for compatibility.
- Record the selected backend in state and run manifests.
- Add regression tests for C# execution ordering, result shape, mutation persistence, and custom backend injection.
