import json
import stat
import zipfile

import pytest

from histra_runner.canonical import canonical_json_bytes
from histra_runner.errors import PackageValidationError
from histra_runner.package import _safe_name, validate_package


def test_validate_package_binds_claim(package_factory, job_document, hrx_bytes, tmp_path):
    package, claim = package_factory(job_document, hrx_bytes)
    contents = validate_package(
        package,
        tmp_path / "unpacked",
        expected_job_id=claim.job_id,
        expected_attempt_id=claim.attempt_id,
        expected_job_sha256=claim.job_sha256,
        expected_hrx_sha256=claim.hrx_sha256,
    )
    assert contents.job == job_document
    assert contents.hrx_path.read_bytes() == hrx_bytes


def test_rejects_claim_mismatch(package_factory, job_document, hrx_bytes, tmp_path):
    package, _ = package_factory(job_document, hrx_bytes)
    with pytest.raises(PackageValidationError, match="job id"):
        validate_package(package, tmp_path / "out", expected_job_id="other")


def test_rejects_noncanonical_job(package_factory, job_document, hrx_bytes, tmp_path):
    package, _ = package_factory(job_document, hrx_bytes)
    with zipfile.ZipFile(package, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["job.json"] = json.dumps(job_document, indent=2).encode()
    with zipfile.ZipFile(package, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(PackageValidationError, match="not canonical"):
        validate_package(package, tmp_path / "out")


@pytest.mark.parametrize("name", ["../evil", "/evil", "a\\b"])
def test_rejects_unsafe_names(name, package_factory, job_document, hrx_bytes, tmp_path):
    package, _ = package_factory(job_document, hrx_bytes)
    with zipfile.ZipFile(package, "a") as archive:
        # ZipInfo's constructor normalizes backslashes on Windows. Preserve the
        # literal archive name so this test exercises the ZIP-path validator.
        info = zipfile.ZipInfo("placeholder")
        info.filename = name
        info.orig_filename = name
        archive.writestr(info, b"bad")
    with pytest.raises(PackageValidationError, match="unsafe ZIP"):
        validate_package(package, tmp_path / "out")


def test_rejects_symlink(package_factory, job_document, hrx_bytes, tmp_path):
    package, _ = package_factory(job_document, hrx_bytes)
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr(info, "model.hrx")
    with pytest.raises(PackageValidationError, match="symbolic links"):
        validate_package(package, tmp_path / "out")


def test_rejects_extra_files_and_size_limit(package_factory, job_document, hrx_bytes, tmp_path):
    package, _ = package_factory(job_document, hrx_bytes)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("extra.txt", b"x")
    with pytest.raises(PackageValidationError, match="exactly"):
        validate_package(package, tmp_path / "out1")
    with pytest.raises(PackageValidationError, match="size limit"):
        validate_package(package, tmp_path / "out2", max_uncompressed_bytes=1)


def test_rejects_invalid_zip_and_missing_metadata(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not zip")
    with pytest.raises(PackageValidationError, match="invalid ZIP"):
        validate_package(bad, tmp_path / "bad-out")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(PackageValidationError, match="must contain"):
        validate_package(empty, tmp_path / "empty-out")


def test_safe_name_rejects_nul():
    with pytest.raises(PackageValidationError, match="unsafe ZIP"):
        _safe_name("a\x00b")
