from pathlib import Path
import json

import pytest

from histra_runner.errors import JobValidationError
from histra_runner.schema import job_spec_from_dict


def base_job() -> dict:
    return {
        "schema_version": "1.0",
        "job_id": "bridge-001",
        "model": {"path": "model.hrx"},
        "analyses": [{"name": "LiveLoad_1"}],
    }


def test_rejects_path_traversal(tmp_path: Path):
    data = base_job()
    data["model"]["path"] = "../outside.hrx"
    spec = job_spec_from_dict(data)
    with pytest.raises(JobValidationError, match="safe relative path"):
        spec.resolve_model_path(tmp_path)


def test_rejects_duplicate_analyses():
    data = base_job()
    data["analyses"].append({"name": "LiveLoad_1"})
    with pytest.raises(JobValidationError, match="Duplicate analysis"):
        job_spec_from_dict(data)


def test_default_outputs_are_lightweight_and_explicit():
    spec = job_spec_from_dict(base_job())
    outputs = spec.analyses[0].outputs
    assert outputs.reactions.enabled
    assert outputs.displacements.enabled
    assert not outputs.modal_contributions.enabled
