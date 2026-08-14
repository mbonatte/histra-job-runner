from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from histra_runner.canonical import canonical_json_bytes, job_sha256, sha256_hex
from histra_runner.contracts import Claim


@pytest.fixture
def job_document() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "job_id": "job-001",
        "model": {
            "template": {"id": "bridge", "sha256": "a" * 64},
            "output_path": "model.hrx",
            "patches": [],
        },
        "workflow": {"analyses": [{"id": "Static"}]},
        "metadata": {},
    }


@pytest.fixture
def hrx_bytes() -> bytes:
    return b"<?xml version='1.0'?><RailBridge />\n"


@pytest.fixture
def package_factory(tmp_path):
    def make(job: dict[str, Any], hrx: bytes, *, name: str = "package.zip") -> tuple[Path, Claim]:
        job_digest = job_sha256(job)
        hrx_digest = sha256_hex(hrx)
        manifest = {
            "protocol_version": "1.0",
            "job_id": job["job_id"],
            "attempt_id": "attempt-001",
            "created_at": "2026-07-31T12:00:00Z",
            "job_sha256": job_digest,
            "hrx": {
                "path": "model.hrx",
                "sha256": hrx_digest,
                "size_bytes": len(hrx),
            },
            "builder": {
                "builder_version": "1.1.0",
                "job_sha256": job_digest,
                "template_id": "bridge",
                "template_sha256": "a" * 64,
                "hrx_sha256": hrx_digest,
                "output_path": "model.hrx",
            },
        }
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            archive.writestr("job.json", canonical_json_bytes(job))
            archive.writestr("model.hrx", hrx)
        claim = Claim(
            job_id=job["job_id"],
            attempt_id="attempt-001",
            job_sha256=job_digest,
            hrx_sha256=hrx_digest,
            lease_expires_at="2026-07-31T13:00:00Z",
            package_url="/jobs/job-001/attempts/attempt-001/package",
        )
        return path, claim

    return make


@pytest.fixture
def valid_package(package_factory, job_document, hrx_bytes):
    """Compatibility fixture retained for the original adapter/worker tests."""
    package, _claim = package_factory(job_document, hrx_bytes)
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return package, manifest
