from __future__ import annotations

from pathlib import Path
import os

import pytest

from histra_runner.backends import PythonBackend, build_backend
from histra_runner.config import BackendConfig, PythonSolverConfig, RunnerConfig
from histra_runner.scour import resolve_foundation_interface_mutations


def test_factory_selects_python_without_csharp_config(tmp_path):
    config = RunnerConfig(
        solver=None,
        workspace_root=tmp_path / "work",
        backend=BackendConfig("python"),
        python=PythonSolverConfig(combination_row=1),
    )
    assert isinstance(build_backend(config), PythonBackend)


def test_scour_resolver_returns_ordered_concrete_assignments(tmp_path):
    from conftest import write_model

    model = tmp_path / "bridge.hrx"
    write_model(
        model,
        [("A", 1, -100, "NotExecutedNotToBeExecuted")],
        with_scour_geometry=True,
    )
    # The compact unit fixture uses textual keys; production HRX interface keys
    # are integers. Replace them here so concrete Python mutations are valid.
    from histra_runner.hrx import read_hrx, write_hrx, detect_xml_encoding

    root = read_hrx(model)
    for number, interface in enumerate(root.iter("Interface"), start=101):
        interface.set("Key", str(number))
    write_hrx(root, model, detect_xml_encoding(model))

    result = resolve_foundation_interface_mutations(
        model, {"Pier_1": {"left": 0.25}}
    )
    assert result["evidence"]["applied"] is True
    assert [item["material_key"] for item in result["assignments"]] == [10, 99]
    default_keys = result["assignments"][0]["interface_keys"]
    scoured_keys = result["assignments"][1]["interface_keys"]
    assert set(default_keys).isdisjoint(scoured_keys)
    assert len(default_keys) + len(scoured_keys) == 5


@pytest.mark.skipif(
    not os.getenv("HISTRA_PYTHON_REPO"),
    reason="set HISTRA_PYTHON_REPO to run the packaged end-to-end backend test",
)
def test_real_python_backend_runs_job_runner_contract(tmp_path, monkeypatch):
    import json
    import shutil
    import sys

    repo = Path(os.environ["HISTRA_PYTHON_REPO"]).resolve()
    monkeypatch.syspath_prepend(str(repo))
    source = repo / "histra" / "model-output" / "model.hrx"
    shutil.copy2(source, tmp_path / "model.hrx")

    from histra_runner.runner import JobRunner
    from histra_runner.schema import AnalysisSpec, JobSpec, MeshSpec, ModelSpec

    config = RunnerConfig(
        solver=None,
        workspace_root=tmp_path / "work",
        backend=BackendConfig("python"),
        python=PythonSolverConfig(1),
    )
    spec = JobSpec(
        schema_version="1.0",
        job_id="python-backend-e2e",
        attempt_id="attempt-1",
        model=ModelSpec("model.hrx"),
        analyses=(AnalysisSpec("Vert", timeout_seconds=300.0),),
        mesh=MeshSpec(enabled=False),
    )
    outcome = JobRunner(config).run(spec, package_root=tmp_path)
    payload = json.loads(outcome.results_path.read_text())
    assert payload["analyses"]["Vert"]["outputs"]["displacements"][-1]["Step"] == 5
    manifest = json.loads(outcome.manifest_path.read_text())
    assert manifest["backend"] == "python"
