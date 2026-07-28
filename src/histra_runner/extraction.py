from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from .errors import ResultExtractionError
from .schema import AnalysisOutputs


@contextmanager
def open_results_database(path: Path) -> Iterator[sqlite3.Connection]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ResultExtractionError(f"Results database not found: {resolved}")
    uri = f"{resolved.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        yield connection
    except sqlite3.Error as exc:
        raise ResultExtractionError(
            f"Could not read results database {resolved}: {exc}"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error as exc:
        raise ResultExtractionError(f"Could not inspect table {table}: {exc}") from exc
    columns = {row[1] for row in rows}
    if not columns:
        raise ResultExtractionError(f"Required results table is missing or empty: {table}")
    return columns


def _select_columns(available: set[str], desired: Sequence[str]) -> list[str]:
    return [column for column in desired if column in available]


def _row_dicts(rows: Sequence[sqlite3.Row]) -> list[dict]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def extract_reactions(
    connection: sqlite3.Connection,
    analysis_key: int,
    *,
    all_steps: bool,
    step: int | None,
) -> list[dict]:
    table = "ReactionSumStates"
    columns = _table_columns(connection, table)
    selected = _select_columns(columns, ["Step", "R1", "R2", "R3"])
    if "Step" not in selected:
        raise ResultExtractionError(f"{table} has no Step column.")
    params: list[object] = [analysis_key]
    where = ["AnalysisKey = ?"]
    if not all_steps:
        if step is None:
            row = connection.execute(
                f'SELECT MAX("Step") FROM "{table}" WHERE "AnalysisKey" = ?',
                (analysis_key,),
            ).fetchone()
            step = row[0] if row is not None else None
        if step is None:
            return []
        where.append("Step = ?")
        params.append(step)
    selected_sql = ", ".join(f'"{column}"' for column in selected)
    sql = (
        f"SELECT {selected_sql} "
        f'FROM "{table}" WHERE {" AND ".join(where)} ORDER BY "Step"'
    )
    return _row_dicts(connection.execute(sql, params).fetchall())


def extract_displacements(
    connection: sqlite3.Connection,
    analysis_key: int,
    *,
    all_steps: bool,
    step: int | None,
    model_point_ids: Sequence[int],
) -> list[dict]:
    table = "DisplModelPoints"
    columns = _table_columns(connection, table)
    desired = ["IdElement", "ParentKey", "Step", "Ux", "Uy", "Uz"]
    selected = _select_columns(columns, desired)
    if "Step" not in selected or "IdElement" not in selected:
        raise ResultExtractionError(f"{table} requires Step and IdElement columns.")
    params: list[object] = [analysis_key]
    where = ["AnalysisKey = ?"]
    if model_point_ids:
        placeholders = ", ".join("?" for _ in model_point_ids)
        where.append(f"IdElement IN ({placeholders})")
        params.extend(model_point_ids)
    if not all_steps:
        if step is None:
            step_query = (
                f'SELECT MAX("Step") FROM "{table}" '
                f'WHERE {" AND ".join(where)}'
            )
            row = connection.execute(step_query, params).fetchone()
            step = row[0] if row is not None else None
        if step is None:
            return []
        where.append("Step = ?")
        params.append(step)
    selected_sql = ", ".join(f'"{column}"' for column in selected)
    sql = (
        f"SELECT {selected_sql} "
        f'FROM "{table}" WHERE {" AND ".join(where)} '
        f'ORDER BY "IdElement", "Step"'
    )
    return _row_dicts(connection.execute(sql, params).fetchall())


def extract_modal_contributions(
    connection: sqlite3.Connection,
    analysis_key: int,
    *,
    top_n: int,
) -> dict:
    table = "ModalValues"
    columns = _table_columns(connection, table)
    desired = ["Step", "Fn", "Mx_pcent", "My_pcent", "Mz_pcent"]
    selected = _select_columns(columns, desired)
    direction_columns = [
        column
        for column in ("Mx_pcent", "My_pcent", "Mz_pcent")
        if column in columns
    ]
    if not direction_columns:
        raise ResultExtractionError(f"{table} has no modal participation columns.")
    selected_sql = ", ".join(f'"{column}"' for column in selected)
    sql = f'SELECT {selected_sql} FROM "{table}" WHERE "AnalysisKey" = ?'
    rows = _row_dicts(connection.execute(sql, (analysis_key,)).fetchall())
    result: dict[str, object] = {"X": [], "Y": [], "Z": [], "Cumulative": {}}
    labels = {"Mx_pcent": "X", "My_pcent": "Y", "Mz_pcent": "Z"}
    cumulative = result["Cumulative"]
    assert isinstance(cumulative, dict)
    for column in direction_columns:
        ranked = sorted(
            rows,
            key=lambda record: float(record.get(column) or 0.0),
            reverse=True,
        )[:top_n]
        result[labels[column]] = ranked
        cumulative[column] = sum(float(record.get(column) or 0.0) for record in rows)
    return result


def extract_analysis_outputs(
    database_path: Path,
    analysis_key: int,
    outputs: AnalysisOutputs,
) -> dict:
    extracted: dict[str, object] = {}
    with open_results_database(database_path) as connection:
        if outputs.displacements.enabled:
            extracted["displacements"] = extract_displacements(
                connection,
                analysis_key,
                all_steps=outputs.displacements.all_steps,
                step=outputs.displacements.step,
                model_point_ids=outputs.displacements.model_point_ids,
            )
        if outputs.reactions.enabled:
            extracted["reactions"] = extract_reactions(
                connection,
                analysis_key,
                all_steps=outputs.reactions.all_steps,
                step=outputs.reactions.step,
            )
        if outputs.modal_contributions.enabled:
            extracted["modal_contributions"] = extract_modal_contributions(
                connection,
                analysis_key,
                top_n=outputs.modal_contributions.top_n,
            )
    return extracted
