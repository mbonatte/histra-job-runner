from __future__ import annotations

from pathlib import Path

from histra_runner.hrx import NOT_TO_RUN, TO_RUN, read_hrx
from histra_runner.runner import JobRunner
from histra_runner.schema import AnalysisSpec, JobSpec, MeshSpec, ModelSpec

from conftest import FakeSolver, write_model


class RecordingScourSolver(FakeSolver):
    def __init__(self) -> None:
        super().__init__()
        self.materials: dict[str, dict[str, str]] = {}

    def run(self, model_path: Path, timeout_seconds: float):
        root = read_hrx(model_path)
        name = next(
            analysis.get("Name", "")
            for analysis in root.iter("Analysis")
            if any(state.get("State") == TO_RUN for state in analysis.iter("State"))
        )
        self.materials[name] = {
            item.get("Key", ""): item.get("MaterialKey", "") for item in root.iter("Interface")
        }
        return super().run(model_path, timeout_seconds)


def test_scour_reset_apply_and_persistence_stay_in_csharp_backend(tmp_path, runner_config):
    model = tmp_path / "bridge.hrx"
    write_model(
        model,
        [
            ("Vert", 1, -100, NOT_TO_RUN),
            ("Scour1", 2, 1, NOT_TO_RUN),
            ("Live1", 3, 2, NOT_TO_RUN),
            ("Scour2", 4, 1, NOT_TO_RUN),
            ("Live2", 5, 4, NOT_TO_RUN),
        ],
        with_scour_geometry=True,
    )
    spec = JobSpec(
        schema_version="1.0",
        job_id="scour-job",
        attempt_id="attempt-1",
        model=ModelSpec("bridge.hrx"),
        analyses=(
            AnalysisSpec("Scour1", interfaces={"Pier_1": {"left": 0.25}}),
            AnalysisSpec("Live1"),
            AnalysisSpec("Scour2", interfaces={"Pier_1": {"right": 0.25}}),
            AnalysisSpec("Live2"),
        ),
        mesh=MeshSpec(enabled=False),
    )
    fake = RecordingScourSolver()

    outcome = JobRunner(runner_config, solver=fake).run(spec, package_root=tmp_path)

    assert fake.executions == ["Vert", "Scour1", "Live1", "Scour2", "Live2"]
    assert fake.materials["Vert"]["L"] == "10"
    assert fake.materials["Scour1"]["L"] == "99"
    assert fake.materials["Scour1"]["R"] == "10"
    assert fake.materials["Live1"]["L"] == "99"
    assert fake.materials["Scour2"]["L"] == "10"
    assert fake.materials["Scour2"]["R"] == "99"
    assert fake.materials["Live2"]["R"] == "99"

    manifest = __import__("json").loads(outcome.manifest_path.read_text())
    assert [item["analysis"] for item in manifest["mutations"]] == fake.executions
    scour1 = next(item for item in manifest["mutations"] if item["analysis"] == "Scour1")
    assert scour1["piers"]["Pier_1"]["scoured_interface_keys"] == ["L"]
