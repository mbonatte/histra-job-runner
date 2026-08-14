"""Optional release gate against a real HRX and the installed histra-python."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from histra_runner.histra_python_backend import HiStrAPythonBackend


@pytest.mark.skipif(
    not os.getenv("HISTRA_REAL_HRX") or not os.getenv("HISTRA_REAL_ANALYSIS"),
    reason="set HISTRA_REAL_HRX and HISTRA_REAL_ANALYSIS for a numerical release gate",
)
def test_real_hrx_executes_with_default_backend(tmp_path):
    pytest.importorskip("histra")
    source = Path(os.environ["HISTRA_REAL_HRX"])
    hrx = tmp_path / source.name
    hrx.write_bytes(source.read_bytes())
    analysis = os.environ["HISTRA_REAL_ANALYSIS"]
    package = SimpleNamespace(
        job={
            "job_id": "real-release-gate",
            "workflow": {
                "analyses": [analysis],
                "outputs": {
                    "reactions": {"enabled": True, "all_steps": True},
                    "displacements": {"enabled": True, "all_steps": True},
                },
            },
        },
        hrx_path=hrx,
        manifest=SimpleNamespace(hrx=SimpleNamespace(path=source.name)),
    )
    result = HiStrAPythonBackend().execute(package, tmp_path / "output")
    assert result.results["backend"] == "histra-python"
    assert analysis in result.results["analyses"]
