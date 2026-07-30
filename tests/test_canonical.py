import pytest

from histra_runner.canonical import canonical_json_bytes, job_sha256


def test_hash_is_independent_of_mapping_order():
    assert job_sha256({"b": 2, "a": 1}) == job_sha256({"a": 1, "b": 2})


def test_non_json_value_is_rejected():
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": object()})
