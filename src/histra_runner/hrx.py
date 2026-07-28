from __future__ import annotations

import codecs
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

from .errors import HrxValidationError

NOT_TO_RUN = "NotExecutedNotToBeExecuted"
TO_RUN = "NotExecutedToBeExecute"
COMPLETED = "ExecutedCompleted"


def detect_xml_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        beginning = handle.read(256)
    if beginning.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if beginning.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    declaration = re.search(
        br'<\?xml[^>]+encoding=["\']([^"\']+)["\']', beginning, re.IGNORECASE
    )
    if declaration:
        return declaration.group(1).decode("ascii")
    return "utf-8"


def read_hrx(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HrxValidationError(f"Could not read HRX file {path}: {exc}") from exc


def write_hrx(root: ET.Element, path: Path, encoding: str | None = None) -> None:
    target_encoding = encoding or detect_xml_encoding(path)
    if target_encoding.lower() == "utf-8-sig":
        target_encoding = "utf-8"
    ET.ElementTree(root).write(path, encoding=target_encoding, xml_declaration=True)


def analyses_by_name(root: ET.Element) -> dict[str, ET.Element]:
    return {
        analysis.get("Name"): analysis
        for analysis in root.iter("Analysis")
        if analysis.get("Name")
    }


def analyses_by_key(root: ET.Element) -> dict[str, ET.Element]:
    return {
        analysis.get("Key"): analysis
        for analysis in root.iter("Analysis")
        if analysis.get("Key")
    }


def set_analysis_states(analysis: ET.Element, state_value: str) -> None:
    states = analysis.find("States")
    if states is None or not states.findall("State"):
        raise HrxValidationError(
            f"Analysis '{analysis.get('Name', '<unnamed>')}' has no executable State entries."
        )
    for state in states.findall("State"):
        state.set("State", state_value)


def _element_is_completed(analysis: ET.Element) -> bool:
    states = analysis.find("States")
    records = states.findall("State") if states is not None else []
    return bool(records) and all(state.get("State") == COMPLETED for state in records)


def select_only_analysis(path: Path, analysis_name: str) -> None:
    """Select one analysis while preserving completed dependency states.

    HiStrA analyses can depend on previous analyses through InitialAnalysisKey.
    Resetting an already completed dependency would discard the state required
    by the next analysis, so only non-completed, non-selected analyses are
    switched off.
    """

    encoding = detect_xml_encoding(path)
    root = read_hrx(path)
    by_name = analyses_by_name(root)
    selected = by_name.get(analysis_name)
    if selected is None:
        raise HrxValidationError(f"Analysis '{analysis_name}' was not found in {path}.")
    for analysis in by_name.values():
        if analysis is selected:
            continue
        if not _element_is_completed(analysis):
            set_analysis_states(analysis, NOT_TO_RUN)
    set_analysis_states(selected, TO_RUN)
    write_hrx(root, path, encoding)


def set_all_analyses_off(path: Path) -> None:
    encoding = detect_xml_encoding(path)
    root = read_hrx(path)
    for analysis in analyses_by_name(root).values():
        set_analysis_states(analysis, NOT_TO_RUN)
    write_hrx(root, path, encoding)


def validate_requested_analyses(path: Path, names: Iterable[str]) -> None:
    root = read_hrx(path)
    available = analyses_by_name(root)
    missing = [name for name in names if name not in available]
    if missing:
        raise HrxValidationError(
            f"HRX file is missing requested analyses: {', '.join(missing)}."
        )


def analysis_key(path: Path, analysis_name: str) -> int:
    root = read_hrx(path)
    analysis = analyses_by_name(root).get(analysis_name)
    if analysis is None:
        raise HrxValidationError(f"Analysis '{analysis_name}' was not found in {path}.")
    raw_key = analysis.get("Key")
    try:
        return int(raw_key)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise HrxValidationError(
            f"Analysis '{analysis_name}' has invalid Key={raw_key!r}."
        ) from exc


def analysis_evidence(path: Path, analysis_name: str) -> dict:
    root = read_hrx(path)
    analysis = analyses_by_name(root).get(analysis_name)
    if analysis is None:
        raise HrxValidationError(f"Analysis '{analysis_name}' was not found in {path}.")
    states = analysis.find("States")
    records = []
    if states is not None:
        for state in states.findall("State"):
            records.append(
                {
                    "id": state.get("Id"),
                    "state": state.get("State"),
                    "exit": state.get("Exit"),
                    "exit_description": state.get("ExitDescription"),
                    "fo": state.get("Fo"),
                }
            )
    return {
        "name": analysis_name,
        "key": analysis.get("Key"),
        "states": records,
        "completed": bool(records)
        and all(record["state"] == COMPLETED for record in records),
    }


def dependency_order(path: Path, requested_names: Iterable[str]) -> list[str]:
    """Return required analyses before dependants, without duplicates."""

    root = read_hrx(path)
    by_name = analyses_by_name(root)
    by_key = analyses_by_key(root)
    ordered: OrderedDict[str, None] = OrderedDict()
    requested_set = set(requested_names)

    def visit(name: str, stack: tuple[str, ...], *, explicitly_requested: bool) -> None:
        if name in stack:
            cycle = " -> ".join((*stack, name))
            raise HrxValidationError(f"Circular analysis dependency: {cycle}")
        analysis = by_name.get(name)
        if analysis is None:
            raise HrxValidationError(f"Analysis '{name}' was not found in {path}.")
        initial_key = analysis.get("InitialAnalysisKey")
        if initial_key and initial_key != "-100":
            required = by_key.get(initial_key)
            if required is None or not required.get("Name"):
                raise HrxValidationError(
                    f"Analysis '{name}' requires missing InitialAnalysisKey='{initial_key}'."
                )
            required_name = required.get("Name")
            assert required_name is not None
            if not _element_is_completed(required) or required_name in requested_set:
                visit(
                    required_name,
                    (*stack, name),
                    explicitly_requested=required_name in requested_set,
                )
        if explicitly_requested or not _element_is_completed(analysis):
            ordered.setdefault(name, None)

    for requested in requested_names:
        visit(requested, (), explicitly_requested=True)
    return list(ordered)
