"""Processing report generation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from thesis2lib.canvas_attachments import attachment_label
from thesis2lib.delivery import (
    delivery_paths_for_student,
    open_access_pdf_filename_for_item,
    resolve_archive_student_number,
    sop_root,
)
from thesis2lib.grade_alignment import (
    ALIGNMENT_ALIGNED,
    ALIGNMENT_MISALIGNED,
    ALIGNMENT_NOT_COMPARABLE,
    joint_individual_alignment,
)
from thesis2lib.grading_checks import (
    FAIL_LABEL,
    individual_criteria_status,
    joint_grade_status,
    student_has_submission,
)
from thesis2lib.excel_export import records_to_dataframe, sanitize_dataframe
from thesis2lib.models import ProcessedStudent, SubmissionRecord
from thesis2lib.package_writer import delivery_package_is_eligible, pdf_package_is_eligible
from thesis2lib.submissions import has_real_submission
from thesis2lib.scoring import (
    format_student_final_score_course,
    format_student_override_score_course,
    format_submission_score_course,
)
from thesis2lib.utils import format_grade_2dp


def second_submission_not_applicable(item: ProcessedStudent) -> bool:
    """Second-slot score/date columns apply only when a final attempt exists."""
    return item.final_attempt is None


def submission_score_label(
    submission: SubmissionRecord | None,
    *,
    exists: bool,
    not_applicable: bool = False,
) -> Any:
    if not_applicable:
        return "Not applicable"
    if not exists:
        return ""
    if submission is None or submission.score is None:
        return ""
    return format_submission_score_course(submission.score)


def submission_date_label(
    submission: SubmissionRecord | None,
    *,
    exists: bool,
    not_applicable: bool = False,
) -> str:
    if not_applicable:
        return "Not applicable"
    if not exists:
        return ""
    return submission.submitted_at if submission and submission.submitted_at else ""


def first_submission_exists(item: ProcessedStudent) -> bool:
    first = item.first_submission
    return has_real_submission(first.raw if first else None)


def second_submission_exists(item: ProcessedStudent) -> bool:
    second = item.second_submission
    return has_real_submission(second.raw if second else None)


def revision_document_found_label(item: ProcessedStudent) -> str:
    if item.final_attempt != "Second":
        return "Not applicable"
    pdf = item.pdf_selection
    if pdf is None:
        return "Not applicable"
    return "Yes" if pdf.revision_documents else "No"


def revision_documents_label(item: ProcessedStudent) -> str:
    pdf = item.pdf_selection
    if pdf is None or not pdf.revision_documents:
        return ""
    return "; ".join(document.name for document in pdf.revision_documents)


def assessment_found_label(item: ProcessedStudent, found: bool) -> str:
    if not item.final_attempt:
        return "Not applicable"
    return "Yes" if found else "No"


def report_snr(item: ProcessedStudent) -> str:
    return resolve_archive_student_number(item)


def validate_report_row(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    student = row.get("Student", "")

    if row.get("Archive info complete") == "Yes" and row.get("Archive missing fields"):
        issues.append(f"{student}: complete but missing fields listed")
    if row.get("Archive info complete") == "No" and row.get("Archive info found") == "Yes":
        if not row.get("Archive missing fields"):
            issues.append(f"{student}: incomplete archive without missing fields")
    if row.get("Revision document found") == "Yes" and not row.get("Revision document(s)"):
        issues.append(f"{student}: revision marked Yes but no filenames")
    if row.get("Revision document found") == "No" and row.get("Revision document(s)"):
        issues.append(f"{student}: revision marked No but filenames present")

    return issues


def build_summary_counts(students: list[ProcessedStudent]) -> pd.DataFrame:
    course_result_keys = {"Passed", "Failed", "Not assessed", "No submission"}
    package_status_keys = {
        "Ready",
        "Manual Review",
        "Missing PDF",
        "Multiple PDFs",
        "Missing assessment",
        "Missing joint assessment",
        "Missing archive info",
        "Incomplete archive info",
    }
    status_counts = {
        "Deliverable packages": 0,
        "Ready": 0,
        "Passed": 0,
        "Failed": 0,
        "Not assessed": 0,
        "No submission": 0,
        "Manual Review": 0,
        "Missing PDF": 0,
        "Multiple PDFs": 0,
        "Missing assessment": 0,
        "Missing joint assessment": 0,
        "Missing archive info": 0,
        "Incomplete archive info": 0,
        "Individual criteria fail": 0,
        "Joint grade fail": 0,
        "Joint vs individual aligned": 0,
        "Joint vs individual misaligned": 0,
        "Joint vs individual not comparable": 0,
    }

    for item in students:
        if item.student.course_result in course_result_keys:
            status_counts[item.student.course_result] += 1
        if item.package_status in package_status_keys:
            status_counts[item.package_status] += 1
        if delivery_package_is_eligible(item):
            status_counts["Deliverable packages"] += 1
        if individual_criteria_status(item)[0] == FAIL_LABEL:
            status_counts["Individual criteria fail"] += 1
        if joint_grade_status(item)[0] == FAIL_LABEL:
            status_counts["Joint grade fail"] += 1
        alignment_status, _ = joint_individual_alignment(item)
        if alignment_status == ALIGNMENT_ALIGNED:
            status_counts["Joint vs individual aligned"] += 1
        elif alignment_status == ALIGNMENT_MISALIGNED:
            status_counts["Joint vs individual misaligned"] += 1
        else:
            status_counts["Joint vs individual not comparable"] += 1

    return pd.DataFrame(
        {"Status": list(status_counts.keys()), "Student Count": list(status_counts.values())}
    )


def student_report_row(item: ProcessedStudent) -> dict[str, Any]:
    first = item.first_submission
    second = item.second_submission
    pdf = item.pdf_selection
    archive = item.archive_metadata

    individual_status, individual_detail = individual_criteria_status(item)
    joint_status, joint_detail = joint_grade_status(item)
    alignment_status, alignment_detail = joint_individual_alignment(item)

    first_exists = first_submission_exists(item)
    second_not_applicable = second_submission_not_applicable(item)
    second_exists = second_submission_exists(item)

    row = {
        "Student": item.student.name,
        "SNR": report_snr(item),
        "Canvas ID": item.student.canvas_id,
        "SIS User ID": item.student.sis_user_id,
        "Section": item.student.section,
        "First submission exists": "Yes" if first_exists else "No",
        "First submission score": submission_score_label(
            first, exists=first_exists
        ),
        "First submitted date": submission_date_label(
            first, exists=first_exists
        ),
        "Second submission exists": (
            "Not applicable"
            if item.final_attempt is None
            else ("Yes" if second_exists else "No")
        ),
        "Second submission score": submission_score_label(
            second,
            exists=second_exists,
            not_applicable=second_not_applicable,
        ),
        "Second submitted date": submission_date_label(
            second,
            exists=second_exists,
            not_applicable=second_not_applicable,
        ),
        "Final attempt": item.final_attempt or "",
        "Final Score": format_student_final_score_course(item.student),
        "Override Score": format_student_override_score_course(item.student),
        "Course Result": item.student.course_result,
        "PDF source": pdf.pdf_source if pdf else "",
        "Canvas attachment names": "; ".join(pdf.canvas_attachment_names) if pdf else "",
        "Local PDF candidates": "; ".join(p.name for p in pdf.candidates) if pdf else "",
        "Revision document found": revision_document_found_label(item),
        "Revision document(s)": revision_documents_label(item),
        "Selected thesis": (
            pdf.selected_path.name
            if pdf and pdf.selected_path
            else attachment_label(pdf.selected_attachment)
            if pdf and pdf.selected_attachment
            else ""
        ),
        "PDF matching method": pdf.matching_method if pdf else "",
        "PDF matching confidence": pdf.confidence if pdf else "",
        "PDF needs review": "Yes" if pdf and pdf.needs_review else ("No" if pdf else ""),
        "PDF content confidence": pdf.content_confidence if pdf else "",
        "PDF content detail": pdf.content_detail if pdf else "",
        "First reader assessment found": assessment_found_label(
            item, item.first_reader_assessment_found
        ),
        "Second reader assessment found": assessment_found_label(
            item, item.second_reader_assessment_found
        ),
        "Individual criteria pass": individual_status,
        "Individual criteria detail": individual_detail,
        "Supervisor name found": assessment_found_label(item, item.supervisor_name_found),
        "Second reader name found": assessment_found_label(
            item, item.second_reader_name_found
        ),
        "Joint assessment found": assessment_found_label(item, item.joint_assessment_found),
        "Joint grade pass": joint_status,
        "Joint grade detail": joint_detail,
        "Joint vs individual alignment": alignment_status,
        "Joint vs individual detail": alignment_detail,
        "Archive info found": (
            "No"
            if archive is None or not archive.found
            else "Yes"
        ),
        "Archive info complete": (
            "Yes"
            if archive and archive.found and archive.complete
            else "No"
        ),
        "Archive missing fields": (
            ", ".join(archive.missing_fields)
            if archive and archive.missing_fields
            else ""
        ),
        "Archive submission date": archive.submission_date if archive else "",
        "Thesis title": item.thesis_title if pdf_package_is_eligible(item) else "",
        "Thesis title confidence": (
            item.thesis_title_confidence if pdf_package_is_eligible(item) else ""
        ),
        "Thesis title method": (
            item.thesis_title_method if pdf_package_is_eligible(item) else ""
        ),
        "Thesis title needs review": (
            "Yes" if pdf_package_is_eligible(item) and item.thesis_title_needs_review else (
                "No" if pdf_package_is_eligible(item) else ""
            )
        ),
        "Package Status": item.package_status,
        "Details": item.details,
    }

    if pdf_package_is_eligible(item):
        sop = sop_root(Path("."))
        delivery = delivery_paths_for_student(sop, item)
        pdf_name = (
            open_access_pdf_filename_for_item(item)
            if item.thesis_title
            else ""
        )
        row["Open Access PDF"] = pdf_name
        row["SOP folder"] = f"SOP/{delivery.folder.name}"
        row["SOP assessments file"] = delivery.assessments.name
    else:
        row["Open Access PDF"] = ""
        row["SOP folder"] = ""
        row["SOP assessments file"] = ""

    return row


def autosize_workbook(path: Path, sheet_names: list[str]) -> None:
    wb = load_workbook(path)
    for sheet_name in sheet_names:
        ws = wb[sheet_name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, column_cells in enumerate(ws.columns, start=1):
            longest = max(len(str(cell.value or "")) for cell in column_cells)
            width = min(max(longest + 2, 12), 55)
            ws.column_dimensions[get_column_letter(index)].width = width
    wb.save(path)


COURSE_RESULT_SORT_ORDER = {
    "Passed": 0,
    "Failed": 1,
    "Not assessed": 2,
    "No submission": 3,
}


def report_student_sort_key(item: ProcessedStudent) -> tuple[int, int, str]:
    """Submitted first, then Passed before Failed; alphabetical within each group."""
    submitted_first = 0 if student_has_submission(item) else 1
    result_rank = COURSE_RESULT_SORT_ORDER.get(item.student.course_result, 99)
    return (submitted_first, result_rank, item.student.name.casefold())


def write_processing_report(
    students: list[ProcessedStudent],
    report_path: Path,
) -> None:
    ordered_students = sorted(students, key=report_student_sort_key)
    rows = [student_report_row(item) for item in ordered_students]
    for row in rows:
        for issue in validate_report_row(row):
            logging.getLogger(__name__).warning("Report consistency: %s", issue)

    students_df = records_to_dataframe(rows)
    summary_df = sanitize_dataframe(build_summary_counts(students))

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        students_df.to_excel(writer, sheet_name="Students", index=False)

    autosize_workbook(report_path, ["Summary", "Students"])
