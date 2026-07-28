# Changelog

## 0.5.0

- Add the installable in-process `PythonBackend` selected by `[backend].type`.
- Add Python-specific configuration without requiring `SolverHistra.exe`.
- Resolve the existing semantic scour procedure to concrete interface/material keys without modifying the HRX.
- Keep committed displacement and constitutive state in `histra-python` across dependency analyses.
- Emit C#-compatible reaction and model-point displacement output rows.
- Treat mesh as in-process preprocessing for Python while preserving the C# `StartMesh` workflow.
- Add Python backend capability metadata, documentation, examples, and end-to-end tests.

## 0.4.0

- Add the backend-neutral `SolverBackend` protocol and immutable request/result types.
- Add `CSharpBackend`, preserving the existing `SolverHistra.exe` subprocess workflow.
- Refactor `JobRunner` so it owns workspace/package lifecycle while the backend owns numerical execution, HRX mutation, validation, and result extraction.
- Preserve the legacy `JobRunner(config, solver=...)` injection path for compatibility.
- Record the selected backend in state and run manifests.
- Add regression tests for C# execution ordering, result shape, mutation persistence, and custom backend injection.
