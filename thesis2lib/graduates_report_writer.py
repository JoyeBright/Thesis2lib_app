"""Graduates report for library Open Access handoff."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from thesis2lib.delivery import (
    open_access_pdf_filename_for_item,
    open_access_root,
    resolve_archive_student_number,
    resolve_library_student_name,
    student_final_grade,
)
from thesis2lib.excel_export import records_to_dataframe
from thesis2lib.models import ProcessedStudent
from thesis2lib.package_writer import delivery_package_is_eligible


def resolve_final_grade(item: ProcessedStudent) -> str:
    return student_final_grade(item)


def graduates_report_rows(
    students: list[ProcessedStudent],
    output_folder: Path,
    semester: str,
    programme: str,
) -> list[dict[str, str]]:
    open_access_folder = open_access_root(output_folder, semester, programme)
    rows: list[dict[str, str]] = []

    for item in students:
        if item.student.course_result != "Passed":
            continue

        package_created = delivery_package_is_eligible(item) and bool(item.thesis_title)
        pdf_name = (
            open_access_pdf_filename_for_item(item)
            if package_created
            else ""
        )

        rows.append(
            {
                "Student name": item.student.name,
                "SNR": resolve_archive_student_number(item),
                "Canvas ID": item.student.canvas_id,
                "SIS User ID": item.student.sis_user_id,
                "Final attempt": item.final_attempt or "",
                "Course final grade": resolve_final_grade(item),
                "Thesis title": item.thesis_title if package_created else "",
                "Thesis PDF": pdf_name,
                "Open Access folder": open_access_folder.name if package_created else "",
            }
        )

    return rows


def autosize_graduates_workbook(path: Path) -> None:
    wb = load_workbook(path)
    ws = wb["Graduates"]
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, column_cells in enumerate(ws.columns, start=1):
        longest = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 14), 50)
    wb.save(path)


def write_graduates_report(
    students: list[ProcessedStudent],
    output_folder: Path,
    destination: Path,
    semester: str,
    programme: str,
    overwrite: bool,
) -> Path | None:
    rows = graduates_report_rows(students, output_folder, semester, programme)
    if not rows:
        return None

    if destination.exists() and not overwrite:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    records_to_dataframe(rows).to_excel(destination, sheet_name="Graduates", index=False)
    autosize_graduates_workbook(destination)
    return destination
