from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from histra_runner.contracts import job_sha256, validate_package_manifest
from histra_runner.errors import PackageError


def _write_package(root: Path) -> dict:
    root.mkdir()
    hrx = b"<Model WizardType='RailBridge'/>"
    (root / "model.hrx").write_bytes(hrx)
    job = {
        "schema_version": "1.0",
        "job_id": "bridge-1",
        "attempt_id": "attempt-1",
        "model": {"path": "model.hrx"},
        "mesh": {"enabled": False},
        "scour": {"foundation_interface_materials": []},
        "analyses": [],
        "validation": {},
        "metadata": {},
    }
    digest = job_sha256(job)
    job["metadata"] = {
        "job_sha256": digest,
        "provenance": {
            "job_sha256": digest,
            "hrx_sha256": hashlib.sha256(hrx).hexdigest(),
            "builder_version": "0.6.0",
            "package_protocol_version": "1.1",
        },
    }
    manifest = {
        "manifest_version": "1.0",
        "protocol_version": "1.1",
        "job_id": "bridge-1",
        "attempt_id": "attempt-1",
        "job_schema_version": "1.0",
        "job_sha256": digest,
        "hrx_path": "model.hrx",
        "hrx_sha256": hashlib.sha256(hrx).hexdigest(),
        "hrx_size_bytes": len(hrx),
        "builder_version": "0.6.0",
    }
    (root / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_validates_ephemeral_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    expected = _write_package(package)

    actual = validate_package_manifest(
        package,
        expected_job_id="bridge-1",
        expected_attempt_id="attempt-1",
    )

    assert actual["validated"] is True
    assert actual["job_sha256"] == expected["job_sha256"]


def test_rejects_modified_hrx(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package(package)
    (package / "model.hrx").write_bytes(b"changed")

    with pytest.raises(PackageError, match="HRX hash"):
        validate_package_manifest(
            package,
            expected_job_id="bridge-1",
            expected_attempt_id="attempt-1",
        )


def test_rejects_modified_job(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package(package)
    job = json.loads((package / "job.json").read_text(encoding="utf-8"))
    job["analyses"] = [{"name": "unexpected"}]
    (package / "job.json").write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(PackageError, match="immutable server JOB"):
        validate_package_manifest(
            package,
            expected_job_id="bridge-1",
            expected_attempt_id="attempt-1",
        )


def test_legacy_package_is_temporarily_supported(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    result = validate_package_manifest(
        package,
        expected_job_id="bridge-1",
        expected_attempt_id="attempt-1",
    )
    assert result == {
        "manifest_version": "legacy",
        "protocol_version": "legacy-1.0",
        "validated": False,
    }
