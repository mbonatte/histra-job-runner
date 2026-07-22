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


def test_preserves_existing_scour_names_and_numeric_shorthand():
    data = base_job()
    data["analyses"][0]["interfaces"] = {
        "pier_1": {"uniform": 0.2, "upstream": 0.1},
        "pier_2": 0.3,
    }
    data["scour"] = {
        "foundation_interface_materials": ["Foundation_Soil", "Soil"],
        "scoured_foundation_interface_material": "Soil_removed",
    }

    spec = job_spec_from_dict(data)
    assert spec.analyses[0].interfaces == {
        "pier_1": {"uniform": 0.2, "upstream": 0.1},
        "pier_2": 0.3,
    }
    assert spec.scour.foundation_interface_materials == ("Foundation_Soil", "Soil")
    assert spec.scour.scoured_foundation_interface_material == "Soil_removed"


def test_rejects_invalid_scour_mode_and_delta():
    data = base_job()
    data["analyses"][0]["interfaces"] = {"pier_1": {"diagonal": 0.2}}
    with pytest.raises(JobValidationError, match="unsupported mode"):
        job_spec_from_dict(data)

    data = base_job()
    data["analyses"][0]["interfaces"] = {"pier_1": {"left": 1.2}}
    with pytest.raises(JobValidationError, match="between 0 and 1"):
        job_spec_from_dict(data)
