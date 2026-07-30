from __future__ import annotations

import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import canonical_json_bytes, job_sha256, sha256_hex
from .contracts import PackageManifest
from .errors import PackageValidationError


@dataclass(frozen=True)
class PackageContents:
    manifest: PackageManifest
    job: dict[str, Any]
    root: Path
    job_path: Path
    hrx_path: Path


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise PackageValidationError(f"unsafe ZIP entry: {name!r}")
    return path


def _extract_safely(
    package_path: Path,
    destination: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
) -> set[str]:
    destination.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    total = 0
    try:
        archive = zipfile.ZipFile(package_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageValidationError(f"invalid ZIP package: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > max_files:
            raise PackageValidationError("package contains too many files")
        for info in infos:
            path = _safe_name(info.filename)
            if info.filename in names:
                raise PackageValidationError(f"duplicate ZIP entry: {info.filename}")
            names.add(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PackageValidationError(f"symbolic links are forbidden: {info.filename}")
            if info.is_dir():
                continue
            total += info.file_size
            if total > max_uncompressed_bytes:
                raise PackageValidationError("package exceeds uncompressed size limit")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            if len(data) != info.file_size:
                raise PackageValidationError(f"truncated ZIP entry: {info.filename}")
            target.write_bytes(data)
    return names


def validate_package(
    package_path: str | Path,
    destination: str | Path,
    *,
    expected_job_id: str | None = None,
    expected_attempt_id: str | None = None,
    expected_job_sha256: str | None = None,
    expected_hrx_sha256: str | None = None,
    max_files: int = 16,
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024,
) -> PackageContents:
    """Safely extract and cryptographically bind a package to its lease."""
    package_path = Path(package_path)
    destination = Path(destination)
    names = _extract_safely(
        package_path,
        destination,
        max_files=max_files,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    required = {"manifest.json", "job.json"}
    if not required.issubset(names):
        raise PackageValidationError("package must contain manifest.json and job.json")

    try:
        manifest = PackageManifest.model_validate_json(
            (destination / "manifest.json").read_bytes()
        )
        job_bytes = (destination / "job.json").read_bytes()
        job = json.loads(job_bytes)
    except Exception as exc:
        raise PackageValidationError(f"invalid package metadata: {exc}") from exc

    if job_bytes != canonical_json_bytes(job):
        raise PackageValidationError("job.json is not canonical JSON")
    digest = job_sha256(job)
    if digest != manifest.job_sha256:
        raise PackageValidationError("JOB digest does not match manifest")
    if job.get("job_id") != manifest.job_id:
        raise PackageValidationError("JOB identity does not match manifest")

    hrx_rel = _safe_name(manifest.hrx.path)
    expected_names = {"manifest.json", "job.json", manifest.hrx.path}
    if names != expected_names:
        raise PackageValidationError("package must contain exactly manifest, JOB, and declared HRX")
    hrx_path = destination.joinpath(*hrx_rel.parts)
    try:
        hrx = hrx_path.read_bytes()
    except FileNotFoundError as exc:
        raise PackageValidationError("declared HRX does not exist") from exc
    if len(hrx) != manifest.hrx.size_bytes:
        raise PackageValidationError("HRX size does not match manifest")
    if sha256_hex(hrx) != manifest.hrx.sha256:
        raise PackageValidationError("HRX digest does not match manifest")

    checks = [
        ("job id", expected_job_id, manifest.job_id),
        ("attempt id", expected_attempt_id, manifest.attempt_id),
        ("JOB digest", expected_job_sha256, manifest.job_sha256),
        ("HRX digest", expected_hrx_sha256, manifest.hrx.sha256),
    ]
    for label, expected, actual in checks:
        if expected is not None and expected != actual:
            raise PackageValidationError(f"{label} does not match the active claim")

    return PackageContents(
        manifest=manifest,
        job=job,
        root=destination,
        job_path=destination / "job.json",
        hrx_path=hrx_path,
    )
