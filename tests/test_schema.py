from __future__ import annotations

import pytest

from histra_runner.errors import JobValidationError
from histra_runner.schema import job_spec_from_dict


def base_job():
    return {
        "schema_version": "1.0",
        "job_id": "job-1",
        "model": {"path": "model.hrx"},
        "mesh": {"enabled": False},
        "analyses": [{"name": "A", "timeout_seconds": 10}],
    }


def test_parses_directional_scour_and_outputs():
    data = base_job()
    data["analyses"][0]["interfaces"] = {"Pier_1": {"left": 0.25}}
    data["analyses"][0]["outputs"] = {
        "reactions": {"all_steps": False},
        "displacements": {"model_point_ids": [1, 2]},
        "modal_contributions": {"enabled": True, "top_n": 2},
    }
    spec = job_spec_from_dict(data)
    analysis = spec.analyses[0]
    assert analysis.interfaces == {"Pier_1": {"left": 0.25}}
    assert analysis.outputs.displacements.model_point_ids == (1, 2)
    assert analysis.outputs.modal_contributions.top_n == 2


@pytest.mark.parametrize(
    "mutator",
    [
        lambda job: job.update(schema_version="2.0"),
        lambda job: job.update(job_id="bad id"),
        lambda job: job["analyses"].append({"name": "A"}),
        lambda job: job["analyses"][0].update(timeout_seconds=0),
        lambda job: job["analyses"][0].update(interfaces={"Pier_1": {"bad": 0.1}}),
    ],
)
def test_rejects_invalid_jobs(mutator):
    data = base_job()
    mutator(data)
    with pytest.raises(JobValidationError):
        job_spec_from_dict(data)
