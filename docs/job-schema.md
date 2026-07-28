# Job schema 1.0

A job package contains `job.json` and the referenced HRX model. The immutable external schema remains unchanged by the backend refactor.

Required fields are `schema_version`, `job_id`, `model.path`, and a non-empty `analyses` array. Each analysis supplies a name, timeout, optional foundation-interface mutations, and requested outputs.

The runner converts this schema into three backend-neutral values:

- `AnalysisPlan`: ordered requested analyses, optional mesh analysis, timeouts, and validation policy;
- `MutationSchedule`: per-analysis interface scenarios and material names;
- `OutputRequest`: per-analysis reaction, displacement, and modal selections.

Backend implementations do not read `job.json` directly.
