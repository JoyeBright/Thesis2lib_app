"""Write SOP archive metadata files into student packages."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from thesis2lib.excel_export import records_to_dataframe
from thesis2lib.models import ArchiveMetadata, ProcessedStudent
from thesis2lib.scoring import (
    canvas_submission_raw_fields,
    format_course_final_grade_ten_scale,
)
from thesis2lib.utils import sanitize_folder_name


def archive_metadata_rows(
    metadata: ArchiveMetadata,
    *,
    thesis_title: str = "",
    first_assignment_score: float | None = None,
    second_assignment_score: float | None = None,
    course_final_grade: str = "",
) -> list[dict[str, str]]:
    """Return the SOP archive-information fields for library delivery."""
    resolved_title = thesis_title or metadata.thesis_title
    first_fields = canvas_submission_raw_fields(first_assignment_score)
    second_fields = canvas_submission_raw_fields(second_assignment_score)
    rows = [
        {"Field": "Student Name", "Value": metadata.student_name},
        {"Field": "Canvas ID", "Value": metadata.canvas_id},
        {"Field": "SIS User ID", "Value": metadata.sis_user_id},
        {"Field": "SIS Login ID", "Value": metadata.sis_login_id},
        {"Field": "Section", "Value": metadata.section},
        {"Field": "Supervisor / First-reader Name", "Value": metadata.supervisor_name},
        {"Field": "Second-reader Name", "Value": metadata.second_reader_name},
        {"Field": "Third Reader (optional)", "Value": metadata.third_reader_name},
        {"Field": "Submission Date", "Value": metadata.submission_date},
        {
            "Field": "1st assignment score",
            "Value": (
                f"{first_fields['Score']} / {first_fields['Out of']}"
                if first_fields["Score"]
                else ""
            ),
        },
        {
            "Field": "1st assignment (0-10)",
            "Value": first_fields["Grade out of 10"],
        },
        {
            "Field": "2nd assignment score",
            "Value": (
                f"{second_fields['Score']} / {second_fields['Out of']}"
                if second_fields["Score"]
                else ""
            ),
        },
        {
            "Field": "2nd assignment (0-10)",
            "Value": second_fields["Grade out of 10"],
        },
        {"Field": "Final Grade", "Value": course_final_grade},
    ]
    if resolved_title:
        rows.append({"Field": "Thesis Title", "Value": resolved_title})
    return rows


def minimal_archive_metadata(item: ProcessedStudent) -> ArchiveMetadata:
    """Build archive metadata from overview and/or Canvas when rows are needed."""
    if item.archive_metadata is not None:
        return item.archive_metadata

    from thesis2lib.delivery import resolve_archive_student_number

    student = item.student
    return ArchiveMetadata(
        student_name=student.name,
        canvas_id=student.canvas_id,
        sis_user_id=resolve_archive_student_number(item),
        sis_login_id="",
        section=student.section,
        supervisor_name="",
        second_reader_name="",
        third_reader_name="",
        submission_date="",
        final_grade="",
        thesis_title="",
        source_sheet="",
        found=False,
    )


def archive_rows_for_student(
    item: ProcessedStudent,
    *,
    thesis_title: str = "",
) -> list[dict[str, str]]:
    """Archive Information rows for any student, using Canvas gradebook scores."""
    return archive_metadata_rows(
        minimal_archive_metadata(item),
        thesis_title=thesis_title or item.thesis_title,
        first_assignment_score=item.student.first_assignment_score,
        second_assignment_score=item.student.second_assignment_score,
        course_final_grade=format_course_final_grade_ten_scale(item.student),
    )


def autosize_archive_workbook(path: Path) -> None:
    wb = load_workbook(path)
    ws = wb["Archive Information"]
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, column_cells in enumerate(ws.columns, start=1):
        longest = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 18), 60)
    wb.save(path)


def write_archive_info_file(
    metadata: ArchiveMetadata,
    student_folder: Path,
    student_id: str,
    overwrite: bool,
    destination: Path | None = None,
    *,
    first_assignment_score: float | None = None,
    second_assignment_score: float | None = None,
    course_final_grade: str = "",
) -> Path:
    target = destination or (
        student_folder / f"{sanitize_folder_name(student_id)}_Archive_Info.xlsx"
    )
    if target.exists() and not overwrite:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    df = records_to_dataframe(
        archive_metadata_rows(
            metadata,
            first_assignment_score=first_assignment_score,
            second_assignment_score=second_assignment_score,
            course_final_grade=course_final_grade,
        )
    )
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Archive Information", index=False)

    autosize_archive_workbook(target)
    return target
