# Job schema 1.0

## Required fields

- `schema_version`: currently `"1.0"`.
- `job_id`: filesystem-safe identifier, maximum 128 characters.
- `model.path`: safe relative path inside the package.
- `analyses`: non-empty ordered list of requested analyses.

## Model integrity

`model.sha256` is optional during local development. When present, the runner
checks it before staging the HRX. The network server should always populate it.

## Mesh

The default mesh analysis is `StartMesh`. Set `mesh.enabled` to `false` only
when the supplied model is already in the exact state required for subsequent
analyses. Foundation-interface scour is applied after the mesh step, immediately
before each analysis step.

## Dependencies and order

The runner reads `InitialAnalysisKey` from the HRX. Required analyses are run
before requested dependants and are de-duplicated. Outputs are extracted only
for analyses explicitly listed in `analyses`.

The order of explicitly requested analyses remains significant because the
foundation-interface material state persists. Place all analyses that use one
scour state before the next analysis that changes that state.

## Scour material names

The optional top-level `scour` object uses the same names as the supplied code:

```json
{
  "scour": {
    "foundation_interface_materials": ["Foundation_Soil", "Soil"],
    "scoured_foundation_interface_material": "Soil_removed"
  }
}
```

The listed default materials are tried in order. The first one present in the
HRX is used to restore the bottom interfaces of a pier before the new scour
selection is applied.

## Per-analysis foundation interfaces

Use `interfaces` on an analysis entry:

```json
{
  "name": "Scour_1",
  "interfaces": {
    "pier_1": {
      "left": 0.20,
      "upstream": 0.10
    },
    "pier_2": 0.30
  }
}
```

Names and meanings are unchanged from the supplied automation code:

- `pier_1`, `pier_2`, and so on identify piers by HRX geometry order.
- `uniform`, `left`, `right`, `upstream`, and `downstream` are supported.
- each delta must be between `0` and `1`;
- a direct numeric value is the backward-compatible shorthand for `uniform`.

Before the solver starts that analysis, the runner calls
`run_update_foundation_ifaces`. An analysis without `interfaces` performs no
mutation and preserves the previous interface state.

## Output selection

Each requested analysis can independently configure:

- `displacements`: `all_steps`, optional `step`, and optional
  `model_point_ids`;
- `reactions`: `all_steps` and optional `step`;
- `modal_contributions`: `enabled` and `top_n`.

The corresponding `results.json` analysis entry includes the interface-mutation
evidence. `run.json` includes evidence for requested analyses and automatically
inserted dependencies.

## Validation

`validation.require_completed_state` requires every HRX state belonging to an
executed analysis to be `ExecutedCompleted`.

`validation.require_results_database` requires the `.Results` SQLite file.
`minimum_results_bytes` provides a minimal size check.
