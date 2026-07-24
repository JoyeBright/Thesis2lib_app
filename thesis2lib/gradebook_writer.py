"""Write a Canvas gradebook export matching the current course gradebook."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from thesis2lib.canvas_client import CanvasClient
from thesis2lib.canvas_records import deduplicate_enrollments, filter_student_enrollments
from thesis2lib.excel_export import records_to_dataframe
from thesis2lib.progress import ConsoleProgress
from thesis2lib.utils import clean_text, format_grade_2dp, normalize_identifier


def grade_cell(raw: dict | None) -> str:
    if not raw:
        return ""
    grade = clean_text(raw.get("grade"))
    if grade:
        return grade
    score = raw.get("score")
    if score is not None and score != "":
        return str(score)
    return ""


def grades_cell(grades: dict, key: str) -> str:
    value = grades.get(key)
    if value is None or value == "":
        return ""
    return str(value)


def assignment_headers(assignments: list[dict]) -> list[tuple[str, str]]:
    ordered = sorted(
        assignments,
        key=lambda item: (item.get("position") or 0, item.get("id") or 0),
    )
    seen: dict[str, int] = {}
    headers: list[tuple[str, str]] = []
    for assignment in ordered:
        assignment_id = normalize_identifier(assignment.get("id"))
        if not assignment_id:
            continue
        name = clean_text(assignment.get("name")) or f"Assignment {assignment_id}"
        count = seen.get(name, 0)
        if count:
            label = f"{name} ({count})"
        else:
            label = name
        seen[name] = count + 1
        headers.append((assignment_id, label))
    return headers


def submission_lookup(submissions: list[dict]) -> dict[tuple[str, str], dict]:
    mapping: dict[tuple[str, str], dict] = {}
    for submission in submissions:
        user_id = normalize_identifier(submission.get("user_id"))
        assignment_id = normalize_identifier(submission.get("assignment_id"))
        if user_id and assignment_id:
            mapping[(user_id, assignment_id)] = submission
    return mapping


def build_gradebook_rows(
    enrollments: list[dict],
    assignments: list[dict],
    submissions: list[dict],
    derived_scores: dict[str, tuple[float | None, float | None]] | None = None,
) -> list[dict[str, str]]:
    assignment_columns = assignment_headers(assignments)
    submissions_by_key = submission_lookup(submissions)
    rows: list[dict[str, str]] = []

    for enrollment in sorted(
        enrollments,
        key=lambda item: clean_text((item.get("user") or {}).get("sortable_name")),
    ):
        user = enrollment.get("user") or {}
        grades = enrollment.get("grades") or {}
        canvas_id = normalize_identifier(user.get("id") or enrollment.get("user_id"))

        row: dict[str, str] = {
            "Student": clean_text(user.get("sortable_name") or user.get("name")),
            "ID": canvas_id,
            "SIS User ID": clean_text(user.get("sis_user_id")),
            "SIS Login ID": clean_text(user.get("sis_login_id")),
            "Section": clean_text(
                enrollment.get("sis_section_id")
                or enrollment.get("course_section_id")
                or enrollment.get("section")
            ),
        }

        for assignment_id, header in assignment_columns:
            row[header] = grade_cell(submissions_by_key.get((canvas_id, assignment_id)))

        derived_final, derived_override = (None, None)
        if derived_scores:
            derived_final, derived_override = derived_scores.get(canvas_id, (None, None))

        final_score = grades_cell(grades, "final_score")
        if not final_score and derived_final is not None:
            final_score = format_grade_2dp(derived_final)
        override_score = grades_cell(grades, "override_score")
        if not override_score and derived_override is not None:
            override_score = format_grade_2dp(derived_override)

        row.update(
            {
                "Current Score": grades_cell(grades, "current_score"),
                "Final Score": final_score,
                "Current Grade": grades_cell(grades, "current_grade"),
                "Final Grade": grades_cell(grades, "final_grade"),
                "Override Score": override_score,
                "Override Grade": grades_cell(grades, "override_grade"),
            }
        )
        rows.append(row)

    return rows


def autosize_gradebook_workbook(path: Path) -> None:
    wb = load_workbook(path)
    ws = wb["Gradebook"]
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, column_cells in enumerate(ws.columns, start=1):
        longest = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 12), 55)
    wb.save(path)


def write_gradebook(
    client: CanvasClient,
    course_id: int,
    raw_enrollments: list[dict],
    destination: Path,
    overwrite: bool,
    derived_scores: dict[str, tuple[float | None, float | None]] | None = None,
) -> Path:
    if destination.exists() and not overwrite:
        return destination

    student_enrollments = filter_student_enrollments(raw_enrollments)
    enrollments = deduplicate_enrollments(student_enrollments)
    assignments = client.get_assignments(course_id)
    assignment_progress = (
        ConsoleProgress(len(assignments), "Gradebook assignments")
        if assignments
        else None
    )
    submissions = client.get_all_assignment_submissions(
        course_id,
        assignments,
        progress=assignment_progress,
    )
    if assignment_progress:
        assignment_progress.finish()
    rows = build_gradebook_rows(
        enrollments, assignments, submissions, derived_scores=derived_scores
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    records_to_dataframe(rows).to_excel(destination, sheet_name="Gradebook", index=False)
    autosize_gradebook_workbook(destination)
    return destination
