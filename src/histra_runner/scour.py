from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from .errors import HrxValidationError
from .hrx import detect_xml_encoding, read_hrx, write_hrx

logger = logging.getLogger(__name__)

DEFAULT_FOUNDATION_INTERFACE_MATERIALS = ("Foundation_Soil", "Soil")
SCOURED_FOUNDATION_INTERFACE_MATERIAL = "Soil_removed"
SUPPORTED_SCOUR_MODES = ("uniform", "left", "right", "upstream", "downstream")


def masonry_materials(root: ET.Element) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    for elem in root.iter("Template"):
        attrs = elem.attrib
        type_of = attrs.get("TypeOf", "")
        purpose = attrs.get("PurposeType", "")
        name = attrs.get("Name")
        if (
            "MasonryMaterial" in type_of
            or "MasonryMaterial" in purpose
            or name in {*DEFAULT_FOUNDATION_INTERFACE_MATERIALS, SCOURED_FOUNDATION_INTERFACE_MATERIAL}
        ):
            records.append({"Key": attrs.get("Key"), "Name": name})
    return records


def interfaces(root: ET.Element) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    for elem in root.iter("Interface"):
        data = elem.attrib
        if not data.get("Key"):
            continue
        records.append(
            {
                "Key": data.get("Key"),
                "MaterialKey": data.get("MaterialKey"),
                "ParentTypeElement1": data.get("ParentTypeElement1"),
                "VInt3D1": data.get("VInt3D1"),
                "VInt3D2": data.get("VInt3D2"),
                "VInt3D3": data.get("VInt3D3"),
                "VInt3D4": data.get("VInt3D4"),
            }
        )
    return records


def get_foundation_locations(
    root: ET.Element,
) -> dict[str, tuple[float, float, float, float, float]]:
    foundations: dict[str, tuple[float, float, float, float, float]] = {}
    for index, pier in enumerate(root.findall(".//Pier"), start=1):
        reference = pier.find("ReferenceSystem")
        if reference is None:
            raise HrxValidationError(f"pier_{index} has no ReferenceSystem element.")
        origin = reference.get("Origin", "0;0;0")
        try:
            x0, y0, _origin_z = (float(value) for value in origin.split(";"))
            height = float(pier.get("H", 0))
            foundation_height = float(pier.get("Hf", 0))
            length = (
                float(pier.get("B1f", 0))
                + float(pier.get("b2", 0))
                + float(pier.get("B3f", 0))
            )
            width = (
                float(pier.get("W1f", 0))
                + float(pier.get("w2", 0))
                + float(pier.get("W3f", 0))
            )
        except (TypeError, ValueError) as exc:
            raise HrxValidationError(
                f"pier_{index} has invalid foundation geometry or Origin={origin!r}."
            ) from exc
        if length <= 0 or width <= 0:
            raise HrxValidationError(
                f"pier_{index} has invalid foundation dimensions: "
                f"length={length}, width={width}."
            )
        foundations[f"pier_{index}"] = (
            x0,
            y0,
            length,
            width,
            -(height + foundation_height),
        )
    return foundations


def foundation_interfaces(
    root: ET.Element,
) -> dict[str, tuple[list[dict], list[dict], list[dict]]]:
    restraint_interfaces = [
        interface
        for interface in interfaces(root)
        if interface["ParentTypeElement1"] == "Restraint"
    ]
    foundation_locations = get_foundation_locations(root)
    result: dict[str, tuple[list[dict], list[dict], list[dict]]] = {}
    for pier_name, (x0, _y0, length, _width, z0) in foundation_locations.items():
        left_bank: list[dict] = []
        bottom: list[dict] = []
        right_bank: list[dict] = []
        for interface in restraint_interfaces:
            point = interface.get("VInt3D1")
            if not point:
                continue
            try:
                x, _y, z = map(float, point.split(";"))
            except ValueError as exc:
                raise HrxValidationError(
                    f"Interface Key={interface.get('Key')!r} has invalid "
                    f"VInt3D1={point!r}."
                ) from exc
            if x0 - length * 0.55 < x < x0 + length * 0.55:
                if abs(z - z0) < 1:
                    bottom.append(interface)
                elif x < x0:
                    left_bank.append(interface)
                elif x > x0:
                    right_bank.append(interface)
        result[pier_name] = (left_bank, bottom, right_bank)
    return result


def set_material_to_interfaces(
    root: ET.Element, iface_keys: Iterable[str], material_key: str
) -> list[str]:
    keys = set(iface_keys)
    changed: list[str] = []
    for interface in root.iter("Interface"):
        key = interface.get("Key")
        if key and key in keys:
            interface.set("MaterialKey", material_key)
            interface.set("IsPropertyModified", "True")
            changed.append(key)
    return changed


def _get_material_key(root: ET.Element, material_name: str) -> str:
    for material in masonry_materials(root):
        if material["Name"] == material_name and material["Key"]:
            return str(material["Key"])
    raise HrxValidationError(f"Material '{material_name}' not found in the HRX file.")


def _get_first_material_key(root: ET.Element, material_names: tuple[str, ...]) -> str:
    for material_name in material_names:
        try:
            return _get_material_key(root, material_name)
        except HrxValidationError:
            continue
    raise HrxValidationError(
        "None of the default foundation interface materials were found: "
        f"{', '.join(material_names)}."
    )


def _compute_interface_xyz_center(
    interface: dict[str, Any],
) -> tuple[float, float, float]:
    try:
        points = [
            tuple(float(value) for value in str(interface[key]).split(";"))
            for key in ("VInt3D1", "VInt3D2", "VInt3D3", "VInt3D4")
            if interface.get(key) not in {None, ""}
        ]
    except (TypeError, ValueError) as exc:
        raise HrxValidationError(
            f"Interface Key={interface.get('Key')!r} has invalid VInt3D coordinates."
        ) from exc
    if not points or any(len(point) != 3 for point in points):
        raise HrxValidationError(
            f"Interface Key={interface.get('Key')!r} must have three coordinates "
            "per VInt3D point."
        )
    return tuple(
        sum(point[axis] for point in points) / len(points) for axis in range(3)
    )  # type: ignore[return-value]


def _select_outside_delta_interfaces(
    interfaces: list[dict],
    location: tuple[float, float, float, float, float],
    delta: float,
    mode: str = "uniform",
) -> list[str]:
    x0, y0, length, width, _z0 = location
    try:
        delta = float(delta)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"delta must be numeric. Got: {delta!r}") from exc
    if not 0 <= delta <= 1:
        raise ValueError(f"delta must be between 0 and 1. Got: {delta}")
    left_bank_x = x0 - length / 2
    right_bank_x = x0 + length / 2
    upstream_y = y0 - width / 2
    downstream_y = y0 + width / 2
    mode = mode.lower()

    if mode == "uniform":
        half_zone = ((1 - delta) * length) / 2
        min_x = x0 - half_zone
        max_x = x0 + half_zone

        def should_select(xyz_center: tuple[float, float, float]) -> bool:
            return xyz_center[0] < min_x or xyz_center[0] > max_x

    elif mode == "left":
        limit_x = left_bank_x + delta * length

        def should_select(xyz_center: tuple[float, float, float]) -> bool:
            return xyz_center[0] < limit_x

    elif mode == "right":
        limit_x = right_bank_x - delta * length

        def should_select(xyz_center: tuple[float, float, float]) -> bool:
            return xyz_center[0] > limit_x

    elif mode == "upstream":
        limit_y = upstream_y + delta * width

        def should_select(xyz_center: tuple[float, float, float]) -> bool:
            return xyz_center[1] < limit_y

    elif mode == "downstream":
        limit_y = downstream_y - delta * width

        def should_select(xyz_center: tuple[float, float, float]) -> bool:
            return xyz_center[1] > limit_y

    else:
        raise ValueError(
            f"Unsupported scour mode '{mode}'. Expected 'left', 'right', "
            "'uniform', 'upstream', or 'downstream'."
        )
    selected_keys: list[str] = []
    for interface in interfaces:
        if should_select(_compute_interface_xyz_center(interface)):
            key = interface.get("Key")
            if key is not None:
                selected_keys.append(str(key))
    return selected_keys


def set_default_interface(
    root: ET.Element,
    interfaces: list[dict],
    foundation_interface_materials: tuple[
        str, ...
    ] = DEFAULT_FOUNDATION_INTERFACE_MATERIALS,
) -> list[str]:
    interface_keys = [str(item["Key"]) for item in interfaces if item.get("Key")]
    material_key = _get_first_material_key(root, foundation_interface_materials)
    return set_material_to_interfaces(root, interface_keys, material_key)


def update_foundation_interfaces(
    root: ET.Element,
    interface_scenario: dict,
    *,
    foundation_interface_materials: tuple[
        str, ...
    ] = DEFAULT_FOUNDATION_INTERFACE_MATERIALS,
    scoured_foundation_interface_material: str = SCOURED_FOUNDATION_INTERFACE_MATERIAL,
) -> dict[str, Any]:
    """Apply one analysis step's foundation-interface scour mutation.

    The configuration and names intentionally match the supplied automation code:
    ``pier_1`` / ``pier_2`` and the modes ``uniform``, ``left``, ``right``,
    ``upstream`` and ``downstream``. A direct numeric pier value remains the
    backward-compatible shorthand for ``uniform``.
    """

    evidence: dict[str, Any] = {
        "applied": bool(interface_scenario),
        "foundation_interface_materials": list(foundation_interface_materials),
        "scoured_foundation_interface_material": scoured_foundation_interface_material,
        "piers": {},
    }
    if not interface_scenario:
        return evidence
    foundation_locations = get_foundation_locations(root)
    found_interfaces = foundation_interfaces(root)
    scoured_material_key = _get_material_key(
        root, material_name=scoured_foundation_interface_material
    )
    for pier, scour_config in interface_scenario.items():
        logger.info("Processing pier='%s', scour_config='%s'", pier, scour_config)
        resolved_pier = next(
            (name for name in found_interfaces if name.casefold() == str(pier).casefold()),
            None,
        )
        if resolved_pier is None:
            raise HrxValidationError(
                f"Pier '{pier}' was not found in the foundation interfaces."
            )
        if resolved_pier not in foundation_locations:
            raise HrxValidationError(
                f"Pier '{pier}' was not found in the bridge geometry."
            )
        bottom_interfaces = found_interfaces[resolved_pier][1]
        if not bottom_interfaces:
            raise HrxValidationError(
                f"No bottom foundation interfaces were found for pier '{pier}'."
            )
        reset_keys = set_default_interface(
            root,
            bottom_interfaces,
            foundation_interface_materials=foundation_interface_materials,
        )
        scour_items = (
            list(scour_config.items())
            if isinstance(scour_config, dict)
            else [("uniform", scour_config)]
        )
        modes: list[dict[str, Any]] = []
        selected_union: list[str] = []
        selected_seen: set[str] = set()
        for mode, delta in scour_items:
            selected_keys = _select_outside_delta_interfaces(
                bottom_interfaces,
                foundation_locations[resolved_pier],
                delta,
                mode=mode,
            )
            changed_keys = set_material_to_interfaces(
                root, selected_keys, scoured_material_key
            )
            for key in changed_keys:
                if key not in selected_seen:
                    selected_seen.add(key)
                    selected_union.append(key)
            modes.append(
                {
                    "mode": mode,
                    "delta": float(delta),
                    "selected_interface_keys": changed_keys,
                }
            )
        evidence["piers"][pier] = {
            "bottom_interface_count": len(bottom_interfaces),
            "reset_interface_keys": reset_keys,
            "scoured_interface_keys": selected_union,
            "modes": modes,
        }
    return evidence


def run_update_foundation_ifaces(
    in_path: str | Path,
    interface_scenario: dict,
    out_path: str | Path | None = None,
    *,
    foundation_interface_materials: tuple[
        str, ...
    ] = DEFAULT_FOUNDATION_INTERFACE_MATERIALS,
    scoured_foundation_interface_material: str = SCOURED_FOUNDATION_INTERFACE_MATERIAL,
) -> dict[str, Any]:
    """Mutate an HRX file using the original client-side scour operation name."""

    source = Path(in_path)
    target = Path(out_path) if out_path is not None else source
    encoding = detect_xml_encoding(source)
    root = read_hrx(source)
    evidence = update_foundation_interfaces(
        root,
        interface_scenario,
        foundation_interface_materials=foundation_interface_materials,
        scoured_foundation_interface_material=scoured_foundation_interface_material,
    )
    if evidence["applied"]:
        write_hrx(root, target, encoding)
    elif out_path is not None and target != source:
        target.write_bytes(source.read_bytes())
    return evidence


def resolve_foundation_interface_mutations(
    in_path: str | Path,
    interface_scenario: dict,
    *,
    foundation_interface_materials: tuple[str, ...] = DEFAULT_FOUNDATION_INTERFACE_MATERIALS,
    scoured_foundation_interface_material: str = SCOURED_FOUNDATION_INTERFACE_MATERIAL,
) -> dict[str, Any]:
    """Resolve the existing HRX scour procedure into in-memory assignments.

    The resolver executes the same reset-and-scour algorithm against an XML
    copy, then returns ordered concrete interface/material-key assignments for
    an in-process solver. The source HRX is never modified.
    """
    import copy

    root = read_hrx(Path(in_path))
    working = copy.deepcopy(root)
    evidence = update_foundation_interfaces(
        working,
        interface_scenario,
        foundation_interface_materials=foundation_interface_materials,
        scoured_foundation_interface_material=scoured_foundation_interface_material,
    )
    if not evidence["applied"]:
        return {"evidence": evidence, "assignments": []}

    affected: set[str] = set()
    for pier in evidence["piers"].values():
        affected.update(str(key) for key in pier["reset_interface_keys"])
    final_materials: dict[str, list[int]] = {}
    for interface in working.iter("Interface"):
        key = interface.get("Key")
        if key not in affected:
            continue
        material_key = interface.get("MaterialKey")
        if material_key is None:
            raise HrxValidationError(f"Interface Key={key!r} has no MaterialKey.")
        final_materials.setdefault(material_key, []).append(int(str(key)))

    # Apply the default group first and the scoured group second, matching the
    # C# helper's observable reset-then-scour sequence.
    default_key = _get_first_material_key(working, foundation_interface_materials)
    scoured_key = _get_material_key(working, scoured_foundation_interface_material)
    ordered_keys = [default_key, scoured_key]
    assignments = [
        {
            "material_key": int(material_key),
            "interface_keys": sorted(final_materials.get(material_key, [])),
        }
        for material_key in ordered_keys
        if final_materials.get(material_key)
    ]
    return {"evidence": evidence, "assignments": assignments}
