# Job schema 1.0

## Required fields

- `schema_version`: currently `"1.0"`.
- `job_id`: filesystem-safe identifier, maximum 128 characters.
- `model.path`: safe relative path inside the package.
- `analyses`: non-empty list of requested analyses.

## Model integrity

`model.sha256` is optional during local development. When present, the runner
checks it before staging the HRX. The network server should always populate it.

## Mesh

The default mesh analysis is `StartMesh`. Set `mesh.enabled` to `false` only
when the supplied model is already in the exact state required for subsequent
analyses.

## Dependencies

The runner reads `InitialAnalysisKey` from the HRX. Required analyses are run
before requested dependants and are de-duplicated. Outputs are extracted only
for analyses explicitly listed in `analyses`.

## Output selection

Each requested analysis can independently configure:

- `displacements`: `all_steps`, optional `step`, and optional
  `model_point_ids`;
- `reactions`: `all_steps` and optional `step`;
- `modal_contributions`: `enabled` and `top_n`.

## Validation

`validation.require_completed_state` requires every HRX state belonging to an
executed analysis to be `ExecutedCompleted`.

`validation.require_results_database` requires the `.Results` SQLite file.
`minimum_results_bytes` provides a minimal size check.
