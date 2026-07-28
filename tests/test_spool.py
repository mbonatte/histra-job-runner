from __future__ import annotations

import json
import zipfile

import pytest

from histra_runner.contracts import file_sha256, job_sha256
from histra_runner.errors import PackageError
from histra_runner.network import Claim
from histra_runner.spool import AttemptSpool


def claim():
    return Claim("job-1", "attempt-1", "later", "/package", "/heartbeat", "/results", "/failure")


def test_spool_extracts_safe_valid_package(tmp_path):
    spool = AttemptSpool(tmp_path / "spool")
    record = spool.create(claim(), worker_id="w1", server_base_url="https://example.test")
    model_bytes = b"<Model />"
    import hashlib
    job = {
        "schema_version": "1.0",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "model": {"path": "model.hrx", "sha256": hashlib.sha256(model_bytes).hexdigest()},
        "mesh": {"enabled": False},
        "analyses": [{"name": "A"}],
    }
    manifest = {
        "manifest_version": "1.0",
        "protocol_version": "1.1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "job_schema_version": "1.0",
        "job_sha256": job_sha256(job),
        "hrx_path": "model.hrx",
        "hrx_sha256": hashlib.sha256(model_bytes).hexdigest(),
    }
    with zipfile.ZipFile(record.package_zip, "w") as archive:
        archive.writestr("job.json", json.dumps(job))
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("model.hrx", model_bytes)
    assert spool.extract_package(record, maximum_bytes=100_000).name == "job.json"


def test_spool_rejects_path_traversal(tmp_path):
    spool = AttemptSpool(tmp_path / "spool")
    record = spool.create(claim(), worker_id="w1", server_base_url="https://example.test")
    with zipfile.ZipFile(record.package_zip, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with pytest.raises(PackageError):
        spool.extract_package(record, maximum_bytes=100_000)
