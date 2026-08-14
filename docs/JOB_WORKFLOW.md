# JOB workflow contract for histra-python

This document defines the Runner-owned translation between the canonical JOB document and the public histra-python backend API.

## `workflow.analyses`

Required, non-empty array. Each entry is either:

```json
"Vert"
```

or:

```json
{
  "name": "Vert",
  "timeout_seconds": 3600,
  "outputs": {},
  "interface_mutations": []
}
```

`id` is accepted as an alias for `name` for compatibility with existing v1 JOBs. Names are case-insensitively unique and must match HRX analysis definitions.

## Timeouts

Precedence for analysis timeouts:

1. `workflow.analyses[].timeout_seconds`
2. `workflow.analysis_timeout_seconds`
3. Runner `--timeout`
4. 3600 seconds

`workflow.timeout_seconds` controls the whole solver job. When omitted, Runner uses at least the sum of requested analysis timeouts. All timeout values must be positive finite numbers.

## Outputs

`outputs` can be set once at workflow level or overridden by an analysis. `output_request` is accepted as a compatibility alias.

Supported members:

- `reactions`
- `displacements`
- `modal_contributions`

Each may be a Boolean or an object with:

- `enabled`: Boolean
- `all_steps`: Boolean
- `step`: non-negative integer, mutually exclusive with `all_steps=true`

Displacements also accept `model_point_ids`, a unique array of non-negative integer IDs. Empty means all supported model points.

Modal contributions currently default to disabled because the Python solver rejects modal projection.

## Interface mutations

A mutation contains only:

```json
{
  "interface_keys": [1, 2],
  "material_key": 3,
  "preserve_committed_state": true
}
```

It can be attached directly to an analysis or provided in `workflow.interface_mutations` keyed by analysis name. Runner combines top-level mutations first and per-analysis mutations second, preserving declaration order.

No unsupported field is silently discarded.

## Uploaded results

`results.analyses` is keyed by the requested analysis name and contains:

- a compact execution summary;
- the C#-compatible projected outputs returned by histra-python;
- interface mutation reports.

`run` contains provenance and operational metadata, not primary structural response data.

## Failure behavior

Invalid JOB workflows fail before numerical execution with a useful `BackendError`. Unsupported HRX capabilities, nonconvergence, timeout and solver exceptions are reported through the normal Server attempt-failure endpoint. Non-finite or non-JSON result values are rejected rather than uploaded.
