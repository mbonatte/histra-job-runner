from __future__ import annotations

from histra_runner.hrx import read_hrx
from histra_runner.scour import _select_outside_delta_interfaces, run_update_foundation_ifaces

from conftest import write_model


RECORDS = [
    {"Key": "L", "VInt3D1": "-40;10;-5"},
    {"Key": "M", "VInt3D1": "0;10;-5"},
    {"Key": "R", "VInt3D1": "40;10;-5"},
    {"Key": "U", "VInt3D1": "0;2;-5"},
    {"Key": "D", "VInt3D1": "0;18;-5"},
]
LOCATION = (0.0, 10.0, 100.0, 20.0, -5.0)


def test_directional_selector():
    assert _select_outside_delta_interfaces(RECORDS, LOCATION, 0.5) == ["L", "R"]
    assert _select_outside_delta_interfaces(RECORDS, LOCATION, 0.25, mode="left") == ["L"]
    assert _select_outside_delta_interfaces(RECORDS, LOCATION, 0.25, mode="right") == ["R"]
    assert _select_outside_delta_interfaces(RECORDS, LOCATION, 0.25, mode="upstream") == ["U"]
    assert _select_outside_delta_interfaces(RECORDS, LOCATION, 0.25, mode="downstream") == ["D"]


def test_update_resets_previous_scour_before_new_direction(tmp_path):
    model = tmp_path / "model.hrx"
    write_model(model, [("A", 1, -100, "NotExecutedNotToBeExecuted")], with_scour_geometry=True)

    run_update_foundation_ifaces(model, {"Pier_1": {"left": 0.25}})
    first = {i.get("Key"): i.get("MaterialKey") for i in read_hrx(model).iter("Interface")}
    assert first["L"] == "99"

    run_update_foundation_ifaces(model, {"Pier_1": {"right": 0.25}})
    second = {i.get("Key"): i.get("MaterialKey") for i in read_hrx(model).iter("Interface")}
    assert second["L"] == "10"
    assert second["R"] == "99"
