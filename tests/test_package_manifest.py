from __future__ import annotations

import json

import pytest

from histra_runner.contracts import file_sha256, job_sha256, validate_package_manifest
from histra_runner.errors import PackageError


def make_package(tmp_path):
    model = tmp_path / "model.hrx"
    model.write_text("<Model />", encoding="utf-8")
    job = {
        "schema_version": "1.0",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "model": {"path": "model.hrx"},
        "mesh": {"enabled": False},
        "analyses": [{"name": "A"}],
        "metadata": {},
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    manifest = {
        "manifest_version": "1.0",
        "protocol_version": "1.1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "job_schema_version": "1.0",
        "job_sha256": job_sha256(job),
        "hrx_path": "model.hrx",
        "hrx_sha256": file_sha256(model),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return model


def test_manifest_validates_immutable_job_and_model(tmp_path):
    make_package(tmp_path)
    result = validate_package_manifest(
        tmp_path, expected_job_id="job-1", expected_attempt_id="attempt-1"
    )
    assert result["validated"] is True


def test_manifest_rejects_modified_model(tmp_path):
    model = make_package(tmp_path)
    model.write_text("<Changed />", encoding="utf-8")
    with pytest.raises(PackageError):
        validate_package_manifest(
            tmp_path, expected_job_id="job-1", expected_attempt_id="attempt-1"
        )
