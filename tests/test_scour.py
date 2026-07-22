from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from histra_runner.hrx import read_hrx
from histra_runner.scour import (
    _select_outside_delta_interfaces,
    run_update_foundation_ifaces,
)


def _interface_record(key: str, x: float, y: float, z: float = 0.0) -> dict:
    point = f"{x};{y};{z}"
    return {
        "Key": key,
        "VInt3D1": point,
        "VInt3D2": point,
        "VInt3D3": point,
        "VInt3D4": point,
    }


def _write_scour_model(path: Path) -> None:
    root = ET.fromstring(
        """
        <Root>
          <Template Key="10" Name="Foundation_Soil" TypeOf="MasonryMaterial" />
          <Template Key="11" Name="Soil" PurposeType="MasonryMaterial" />
          <Template Key="99" Name="Soil_removed" TypeOf="MasonryMaterial" />
          <Pier H="10" Hf="5" B1f="40" b2="20" B3f="40"
                W1f="5" w2="10" W3f="5">
            <ReferenceSystem Origin="0;10;0" />
          </Pier>
          <Interface Key="left" ParentTypeElement1="Restraint" MaterialKey="99"
                     VInt3D1="-40;10;-15" VInt3D2="-40;10;-15"
                     VInt3D3="-40;10;-15" VInt3D4="-40;10;-15" />
          <Interface Key="middle" ParentTypeElement1="Restraint" MaterialKey="99"
                     VInt3D1="0;10;-15" VInt3D2="0;10;-15"
                     VInt3D3="0;10;-15" VInt3D4="0;10;-15" />
          <Interface Key="right" ParentTypeElement1="Restraint" MaterialKey="99"
                     VInt3D1="40;10;-15" VInt3D2="40;10;-15"
                     VInt3D3="40;10;-15" VInt3D4="40;10;-15" />
          <Interface Key="upstream" ParentTypeElement1="Restraint" MaterialKey="99"
                     VInt3D1="0;2;-15" VInt3D2="0;2;-15"
                     VInt3D3="0;2;-15" VInt3D4="0;2;-15" />
          <Interface Key="downstream" ParentTypeElement1="Restraint" MaterialKey="99"
                     VInt3D1="0;18;-15" VInt3D2="0;18;-15"
                     VInt3D3="0;18;-15" VInt3D4="0;18;-15" />
        </Root>
        """
    )
    ET.ElementTree(root).write(path, encoding="utf-16", xml_declaration=True)


def test_same_scour_mode_names_and_selection_as_supplied_code():
    location = (0.0, 10.0, 100.0, 20.0, -5.0)
    records = [
        _interface_record("left", -40.0, 10.0),
        _interface_record("middle", 0.0, 10.0),
        _interface_record("right", 40.0, 10.0),
        _interface_record("upstream", 0.0, 2.0),
        _interface_record("downstream", 0.0, 18.0),
    ]

    assert _select_outside_delta_interfaces(records, location, 0.5) == ["left", "right"]
    assert _select_outside_delta_interfaces(records, location, 0.25, mode="left") == ["left"]
    assert _select_outside_delta_interfaces(records, location, 0.25, mode="right") == ["right"]
    assert _select_outside_delta_interfaces(records, location, 0.25, mode="upstream") == [
        "upstream"
    ]
    assert _select_outside_delta_interfaces(records, location, 0.25, mode="downstream") == [
        "downstream"
    ]

    with pytest.raises(ValueError, match="upstream.*downstream"):
        _select_outside_delta_interfaces(records, location, 0.25, mode="diagonal")


def test_run_update_foundation_ifaces_resets_then_applies_scour(tmp_path: Path):
    model = tmp_path / "model.hrx"
    _write_scour_model(model)

    evidence = run_update_foundation_ifaces(
        model,
        {"pier_1": {"left": 0.25}},
    )

    root = read_hrx(model)
    material_by_key = {
        interface.get("Key"): interface.get("MaterialKey")
        for interface in root.iter("Interface")
    }
    assert material_by_key["left"] == "99"
    assert material_by_key["middle"] == "10"
    assert material_by_key["right"] == "10"
    assert material_by_key["upstream"] == "10"
    assert material_by_key["downstream"] == "10"
    assert evidence["piers"]["pier_1"]["scoured_interface_keys"] == ["left"]

    # A later analysis step can change the state: the same pier is first reset,
    # then the new right-side scour is applied.
    run_update_foundation_ifaces(model, {"pier_1": {"right": 0.25}})
    root = read_hrx(model)
    material_by_key = {
        interface.get("Key"): interface.get("MaterialKey")
        for interface in root.iter("Interface")
    }
    assert material_by_key["left"] == "10"
    assert material_by_key["right"] == "99"
