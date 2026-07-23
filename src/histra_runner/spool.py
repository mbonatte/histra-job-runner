from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
import json
import shutil
import stat
import zipfile

from .errors import PackageError
from .jsonio import read_json, utc_now_iso, write_json_atomic
from .network import Claim
from .schema import load_job_spec


_TERMINAL_LOCAL_STATES = {"accepted", "failure_reported", "orphaned"}


def _safe_component(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise PackageError(f"Unsafe {label} received from server: {value!r}")
    return value


@dataclass
class AttemptRecord:
    root: Path
    claim: Claim
    worker_id: str
    server_base_url: str
    status: str
    created_at: str
    updated_at: str
    details: dict[str, Any]

    @property
    def directory(self) -> Path:
        return self.root / _safe_component(self.claim.job_id, "job_id") / _safe_component(
            self.claim.attempt_id, "attempt_id"
        )

    @property
    def record_path(self) -> Path:
        return self.directory / "record.json"

    @property
    def package_zip(self) -> Path:
        return self.directory / "package.zip"

    @property
    def package_directory(self) -> Path:
        return self.directory / "package"

    @property
    def job_path(self) -> Path:
        return self.package_directory / "job.json"

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_LOCAL_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "worker_id": self.worker_id,
            "server_base_url": self.server_base_url,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "claim": self.claim.as_dict(),
            "details": self.details,
        }

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.record_path, self.as_dict())

    def transition(self, status: str, **details: Any) -> None:
        self.status = status
        self.updated_at = utc_now_iso()
        if details:
            self.details.update(details)
        self.save()

    @classmethod
    def create(
        cls,
        root: Path,
        claim: Claim,
        *,
        worker_id: str,
        server_base_url: str,
    ) -> "AttemptRecord":
        now = utc_now_iso()
        record = cls(
            root=root.resolve(),
            claim=claim,
            worker_id=worker_id,
            server_base_url=server_base_url.rstrip("/"),
            status="claimed",
            created_at=now,
            updated_at=now,
            details={},
        )
        if record.record_path.exists():
            existing = cls.load(record.record_path)
            if (
                existing.claim != claim
                or existing.worker_id != worker_id
                or existing.server_base_url.rstrip("/") != server_base_url.rstrip("/")
            ):
                raise PackageError(
                    "Existing local attempt record does not match the server claim."
                )
            return existing
        record.save()
        return record

    @classmethod
    def load(cls, path: Path) -> "AttemptRecord":
        try:
            raw = read_json(path)
            claim = Claim.from_dict(raw["claim"])
            record = cls(
                root=path.resolve().parents[2],
                claim=claim,
                worker_id=str(raw["worker_id"]),
                server_base_url=str(raw["server_base_url"]),
                status=str(raw["status"]),
                created_at=str(raw["created_at"]),
                updated_at=str(raw["updated_at"]),
                details=dict(raw.get("details", {})),
            )
        except Exception as exc:
            raise PackageError(f"Could not read attempt record {path}: {exc}") from exc
        if record.record_path != path.resolve():
            raise PackageError(f"Attempt record path does not match its job and attempt IDs: {path}")
        return record


class AttemptSpool:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, claim: Claim, *, worker_id: str, server_base_url: str) -> AttemptRecord:
        return AttemptRecord.create(
            self.root,
            claim,
            worker_id=worker_id,
            server_base_url=server_base_url,
        )

    def records(self, *, include_terminal: bool = False) -> Iterator[AttemptRecord]:
        for path in sorted(self.root.glob("*/*/record.json")):
            record = AttemptRecord.load(path)
            if include_terminal or not record.terminal:
                yield record

    @staticmethod
    def extract_package(record: AttemptRecord, *, maximum_bytes: int) -> Path:
        archive_path = record.package_zip
        if not archive_path.is_file():
            raise PackageError(f"Downloaded package is missing: {archive_path}")
        destination = record.package_directory
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=False)
        total = 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                seen_names: set[str] = set()
                for member in archive.infolist():
                    name = member.filename.replace("\\", "/")
                    relative = PurePosixPath(name)
                    canonical_name = "/".join(relative.parts).casefold()
                    if (
                        relative.is_absolute()
                        or not relative.parts
                        or any(
                            part in {"", ".", ".."} or ":" in part
                            for part in relative.parts
                        )
                    ):
                        raise PackageError(f"Unsafe path in job package: {member.filename!r}")
                    if canonical_name in seen_names:
                        raise PackageError(
                            f"Duplicate path in job package: {member.filename!r}"
                        )
                    seen_names.add(canonical_name)
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise PackageError(
                            f"Symbolic links are not allowed in job packages: {member.filename!r}"
                        )
                    if member.file_size < 0 or total + member.file_size > maximum_bytes:
                        raise PackageError(
                            "Uncompressed job package exceeds configured maximum_package_bytes."
                        )
                    target = destination.joinpath(*relative.parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source, target.open("wb") as output:
                        while True:
                            chunk = source.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > maximum_bytes:
                                raise PackageError(
                                    "Uncompressed job package exceeds configured maximum_package_bytes."
                                )
                            output.write(chunk)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

        job_path = destination / "job.json"
        spec = load_job_spec(job_path)
        if spec.job_id != record.claim.job_id:
            raise PackageError(
                f"Downloaded job_id {spec.job_id!r} does not match claim {record.claim.job_id!r}."
            )
        if spec.attempt_id != record.claim.attempt_id:
            raise PackageError(
                "Downloaded attempt_id does not match the claimed server attempt."
            )
        spec.validate_package(destination, verify_hash=True)
        record.transition("downloaded", package_uncompressed_bytes=total)
        return job_path

    @staticmethod
    def remove_package(record: AttemptRecord) -> None:
        record.package_zip.unlink(missing_ok=True)
        shutil.rmtree(record.package_directory, ignore_errors=True)
