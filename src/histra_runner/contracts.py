"""Runner-side validation for immutable JOBs and disposable packages."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import PackageError
from .jsonio import read_json

RUNNER_VERSION = "0.5.0"
SUPPORTED_PACKAGE_PROTOCOLS = {"1.1"}
SUPPORTED_JOB_SCHEMA_VERSIONS = {"1.0"}
RUNNER_CAPABILITIES = {
    "package-manifest-1.0",
    "scour-foundation-interfaces",
    "displacement-results",
    "reaction-history",
    "modal-contributions",
    "solver-backend-protocol",
    "csharp-backend",
    "python-backend",
    "in-process-analysis-chain",
    "python-model-point-projection",
}


def canonical_job_bytes(value: dict[str, Any]) -> bytes:
    cloned = json.loads(json.dumps(value))
    cloned.pop("attempt_id", None)
    metadata = cloned.setdefault("metadata", {})
    for key in (
        "job_sha256",
        "provenance",
        "submission_protocol",
        "idempotency_key",
        "import_validation",
    ):
        metadata.pop(key, None)
    return json.dumps(
        cloned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def job_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_job_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise PackageError(f"Package manifest is missing string field {key!r}.")
    return item


def validate_package_manifest(
    package_root: Path,
    *,
    expected_job_id: str,
    expected_attempt_id: str,
) -> dict[str, Any]:
    manifest_path = package_root / "manifest.json"
    if not manifest_path.is_file():
        return {
            "manifest_version": "legacy",
            "protocol_version": "legacy-1.0",
            "validated": False,
        }
    try:
        manifest = read_json(manifest_path)
        job = read_json(package_root / "job.json")
    except Exception as exc:
        raise PackageError(f"Could not read package manifest: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(job, dict):
        raise PackageError("manifest.json and job.json must contain JSON objects.")
    if str(manifest.get("manifest_version")) != "1.0":
        raise PackageError("Unsupported package manifest version.")
    protocol = _required_text(manifest, "protocol_version")
    if protocol not in SUPPORTED_PACKAGE_PROTOCOLS:
        raise PackageError(f"Unsupported package protocol {protocol!r}.")
    if _required_text(manifest, "job_id") != expected_job_id:
        raise PackageError("Package manifest job_id does not match the server claim.")
    if _required_text(manifest, "attempt_id") != expected_attempt_id:
        raise PackageError("Package manifest attempt_id does not match the server claim.")
    if str(job.get("job_id")) != expected_job_id:
        raise PackageError("job.json job_id does not match the server claim.")
    if str(job.get("attempt_id")) != expected_attempt_id:
        raise PackageError("job.json attempt_id does not match the server claim.")
    if str(manifest.get("job_schema_version")) not in SUPPORTED_JOB_SCHEMA_VERSIONS:
        raise PackageError("Unsupported job schema version in package manifest.")
    expected_job_hash = _required_text(manifest, "job_sha256")
    actual_job_hash = job_sha256(job)
    if actual_job_hash != expected_job_hash:
        raise PackageError("Package contains a modified immutable server JOB.")
    raw_hrx_path = _required_text(manifest, "hrx_path")
    relative = PurePosixPath(raw_hrx_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise PackageError("Package HRX path is unsafe.")
    hrx_path = (package_root / Path(*relative.parts)).resolve()
    try:
        hrx_path.relative_to(package_root.resolve())
    except ValueError as exc:
        raise PackageError("Package HRX path escapes the package root.") from exc
    if not hrx_path.is_file():
        raise PackageError(f"Package HRX was not found: {hrx_path}")
    expected_hrx_hash = _required_text(manifest, "hrx_sha256")
    actual_hrx_hash = file_sha256(hrx_path)
    if actual_hrx_hash != expected_hrx_hash:
        raise PackageError("Package HRX hash does not match the manifest.")
    return {**manifest, "validated": True}
