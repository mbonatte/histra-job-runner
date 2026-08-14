# Changelog

## 1.1.0 — 2026-07-31

- Make histra-python a required, pinned installation dependency.
- Add the built-in `HiStrAPythonBackend` and select it when no override is supplied.
- Translate canonical JOB analyses, output requests, timeouts, combination rows and concrete interface mutations into the public histra-python API.
- Upload JSON-safe projected reactions, model-point displacements, mutation reports and execution provenance.
- Preserve custom Python and command adapters as explicit overrides.
- Advertise backend and histra-python capabilities during runner registration.
- Retry transient worker-cycle failures without terminating the continuous process.
- Add Windows installation and Scheduled Task guidance.
- Add regression tests and an optional real-HRX numerical release gate.

## 1.0.0

- Introduced canonical package validation, generic execution adapters and the network worker protocol.
