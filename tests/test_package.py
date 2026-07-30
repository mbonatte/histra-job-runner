import copy
import io
import json
import stat
import zipfile

import pytest

from histra_runner.canonical import canonical_json_bytes
from histra_runner.errors import PackageValidationError
from histra_runner.package import validate_package


def test_valid_package_is_extracted_and_bound(valid_package, tmp_path):
    path, manifest = valid_package
    contents = validate_package(
        path,
        tmp_path / "extract",
        expected_job_id="job-001",
        expected_attempt_id="attempt-001",
        expected_job_sha256=manifest["job_sha256"],
        expected_hrx_sha256=manifest["hrx"]["sha256"],
    )
    assert contents.job["workflow"]["analyses"][0]["id"] == "static"
    assert contents.hrx_path.read_bytes() == b"<HiStrAProject/>"


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("protocol_version", "9.9", "metadata"),
        ("job_id", "other", "JOB identity"),
        ("job_sha256", "0" * 64, "JOB digest"),
        ("hrx.size_bytes", 999, "HRX size"),
        ("hrx.sha256", "0" * 64, "metadata"),
        ("builder.output_path", "other.hrx", "metadata"),
    ],
)
def test_manifest_tampering_is_rejected(tmp_path, package_factory, field, value, message):
    path = tmp_path / "bad.zip"
    package_factory(path, manifest_changes={field: value})
    with pytest.raises(PackageValidationError, match=message):
        validate_package(path, tmp_path / "out")


def test_noncanonical_job_is_rejected(tmp_path, package_factory):
    path = tmp_path / "bad.zip"
    package_factory(path, canonical_job=False)
    with pytest.raises(PackageValidationError, match="not canonical"):
        validate_package(path, tmp_path / "out")


def test_tampered_hrx_is_rejected(valid_package, tmp_path):
    path, _ = valid_package
    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.endswith(".hrx"):
                data += b"tamper"
            target.writestr(info.filename, data)
    with pytest.raises(PackageValidationError, match="size"):
        validate_package(rewritten, tmp_path / "out")


def test_claim_identity_mismatch_is_rejected(valid_package, tmp_path):
    path, _ = valid_package
    with pytest.raises(PackageValidationError, match="active claim"):
        validate_package(path, tmp_path / "out", expected_attempt_id="wrong")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "folder\\windows"])
def test_unsafe_paths_are_rejected(tmp_path, package_factory, name):
    path = tmp_path / "bad.zip"
    package_factory(path, extra_entries=[(name, b"bad")])
    with pytest.raises(PackageValidationError, match="unsafe ZIP entry"):
        validate_package(path, tmp_path / "out")


def test_symlinks_are_rejected(tmp_path, package_factory):
    path = tmp_path / "bad.zip"
    mode = (stat.S_IFLNK | 0o777) << 16
    package_factory(path, extra_entries=[("link", b"target", mode)])
    with pytest.raises(PackageValidationError, match="symbolic links"):
        validate_package(path, tmp_path / "out")


def test_extra_files_are_rejected(tmp_path, package_factory):
    path = tmp_path / "bad.zip"
    package_factory(path, extra_entries=[("extra.txt", b"x")])
    with pytest.raises(PackageValidationError, match="exactly"):
        validate_package(path, tmp_path / "out")


def test_size_and_file_limits_are_enforced(tmp_path, package_factory):
    path = tmp_path / "large.zip"
    package_factory(path, hrx=b"x" * 100)
    with pytest.raises(PackageValidationError, match="size limit"):
        validate_package(path, tmp_path / "out1", max_uncompressed_bytes=50)
    with pytest.raises(PackageValidationError, match="too many"):
        validate_package(path, tmp_path / "out2", max_files=2)


def test_invalid_zip_is_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(PackageValidationError, match="invalid ZIP"):
        validate_package(path, tmp_path / "out")


def test_missing_required_metadata_is_rejected(tmp_path):
    path = tmp_path / "missing.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("job.json", b"{}")
    with pytest.raises(PackageValidationError, match="must contain"):
        validate_package(path, tmp_path / "out")


def test_duplicate_entries_are_rejected(tmp_path, package_factory):
    path = tmp_path / "duplicate.zip"
    package_factory(path)
    rewritten = tmp_path / "rewritten.zip"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr("job.json", b"{}")
    with pytest.raises(PackageValidationError, match="duplicate"):
        validate_package(rewritten, tmp_path / "out")
