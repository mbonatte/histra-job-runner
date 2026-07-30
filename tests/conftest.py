import io
import json
import zipfile
from pathlib import Path

import pytest

from histra_runner.canonical import canonical_json_bytes, job_sha256, sha256_hex


@pytest.fixture
def job():
    return {
        "schema_version": "1.0",
        "job_id": "job-001",
        "model": {
            "output_path": "models/job-001.hrx",
            "template": {"id": "base", "sha256": "1" * 64},
            "patches": [],
        },
        "workflow": {"analyses": [{"id": "static"}]},
        "metadata": {},
    }


@pytest.fixture
def package_factory(job):
    def make(
        path: Path,
        *,
        hrx: bytes = b"<HiStrAProject/>",
        job_value=None,
        manifest_changes=None,
        extra_entries=None,
        canonical_job=True,
    ):
        current_job = job_value or job
        job_bytes = canonical_json_bytes(current_job) if canonical_job else json.dumps(current_job, indent=2).encode()
        manifest = {
            "protocol_version": "1.0",
            "job_id": current_job["job_id"],
            "attempt_id": "attempt-001",
            "created_at": "2026-07-30T12:00:00Z",
            "job_sha256": job_sha256(current_job),
            "hrx": {
                "path": current_job["model"]["output_path"],
                "sha256": sha256_hex(hrx),
                "size_bytes": len(hrx),
            },
            "builder": {
                "builder_version": "1.0.0",
                "job_sha256": job_sha256(current_job),
                "template_id": "base",
                "template_sha256": "1" * 64,
                "hrx_sha256": sha256_hex(hrx),
                "output_path": current_job["model"]["output_path"],
            },
        }
        for key, value in (manifest_changes or {}).items():
            target = manifest
            parts = key.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", canonical_json_bytes(manifest))
            archive.writestr("job.json", job_bytes)
            archive.writestr(manifest["hrx"]["path"], hrx)
            for name, data, *attrs in extra_entries or []:
                # ZipInfo(name) normalizes backslashes on Windows. Construct it
                # first, then assign filename directly so the test ZIP contains
                # the exact potentially unsafe archive name.
                info = zipfile.ZipInfo("placeholder")
                info.filename = name
                info.orig_filename = name

                if attrs:
                    info.external_attr = attrs[0]

                archive.writestr(info, data)
        return manifest
    return make


@pytest.fixture
def valid_package(tmp_path, package_factory):
    path = tmp_path / "package.zip"
    manifest = package_factory(path)
    return path, manifest
