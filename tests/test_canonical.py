import math

import pytest

from histra_runner.canonical import canonical_json_bytes, job_sha256, sha256_hex


def test_canonical_json_is_stable():
    left = canonical_json_bytes({"b": 2, "a": "á"})
    right = canonical_json_bytes({"a": "á", "b": 2})
    assert left == right == b'{"a":"\xc3\xa1","b":2}'
    assert job_sha256({"b": 2, "a": "á"}) == sha256_hex(left)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, {1, 2}])
def test_canonical_json_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        canonical_json_bytes({"value": value})
