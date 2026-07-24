"""Optional local assessment workbook reader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from thesis2lib.constants import ASSESSMENT_COLUMN_ALIASES, ASSESSMENT_SHEET_ALIASES
from thesis2lib.models import AssessmentRow
from thesis2lib.utils import clean_text, normalize_identifier, parse_score


def normalize_column_name(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).casefold())


def map_assessment_columns(columns: Iterable[str]) -> dict[str, str]:
    normalized = {normalize_column_name(col): col for col in columns}
    mapping: dict[str, str] = {}
    for field_name, aliases in ASSESSMENT_COLUMN_ALIASES.items():
        for alias in aliases:
            source = normalized.get(alias)
            if source:
                mapping[field_name] = source
                break
    return mapping


def read_assessment_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, keep_default_na=False)
    df.columns = [clean_text(c) for c in df.columns]
    return df


def assessment_rows_from_sheet(
    df: pd.DataFrame,
    column_map: dict[str, str],
) -> list[AssessmentRow]:
    rows: list[AssessmentRow] = []
    for _, record in df.iterrows():

        def get(field_name: str) -> str:
            column = column_map.get(field_name)
            if not column:
                return ""
            return clean_text(record.get(column))

        rows.append(
            AssessmentRow(
                student_id=normalize_identifier(get("student_id")),
                canvas_id=normalize_identifier(get("canvas_id")),
                assessor_role=get("assessor_role"),
                assessor_name=get("assessor_name"),
                criterion_id=get("criterion_id"),
                criterion=get("criterion"),
                score=parse_score(get("score")),
                maximum_score=parse_score(get("maximum_score")),
                motivation=get("motivation"),
                submitted_timestamp=get("submitted_timestamp"),
            )
        )
    return rows


def deduplicate_assessment_rows(
    rows: list[AssessmentRow],
) -> tuple[list[AssessmentRow], bool]:
    grouped: dict[tuple[str, str, str], list[AssessmentRow]] = {}
    for row in rows:
        key = (
            row.student_id or row.canvas_id,
            row.assessor_name,
            row.criterion_id or row.criterion,
        )
        grouped.setdefault(key, []).append(row)

    deduped: list[AssessmentRow] = []
    needs_review = False
    for items in grouped.values():
        if len(items) == 1:
            deduped.append(items[0])
            continue
        timestamps = [
            item.submitted_timestamp for item in items if item.submitted_timestamp
        ]
        if timestamps:
            deduped.append(max(items, key=lambda item: item.submitted_timestamp or ""))
        else:
            needs_review = True
            deduped.extend(items)
    return deduped, needs_review


def load_assessment_workbook(path: Path) -> dict[str, list[AssessmentRow]]:
    workbook: dict[str, list[AssessmentRow]] = {}
    for sheet in ASSESSMENT_SHEET_ALIASES.values():
        df = read_assessment_sheet(path, sheet)
        column_map = map_assessment_columns(df.columns)
        rows = assessment_rows_from_sheet(df, column_map)
        deduped, _ = deduplicate_assessment_rows(rows)
        workbook[sheet] = deduped
    return workbook


def lookup_individual_assessment(
    workbook: dict[str, list[AssessmentRow]] | None,
    final_attempt: str | None,
    sis_user_id: str,
    canvas_id: str,
) -> tuple[bool, bool, bool]:
    if not workbook or not final_attempt:
        return False, False, False

    sheet = "First grading" if final_attempt == "First" else "Second grading"
    rows = workbook.get(sheet, [])
    matched = [
        row
        for row in rows
        if (sis_user_id and row.student_id == sis_user_id)
        or (canvas_id and row.canvas_id == canvas_id)
    ]
    if not matched:
        return False, False, False

    supervisor_found = any(
        "supervisor" in row.assessor_role.casefold()
        or "supervisor" in row.assessor_name.casefold()
        for row in matched
    )
    second_reader_found = any(
        "second" in row.assessor_role.casefold()
        or "reader 2" in row.assessor_role.casefold()
        or "second reader" in row.assessor_name.casefold()
        for row in matched
    )
    return True, supervisor_found, second_reader_found
