"""Write individual (overview) and joint (Canvas) assessment workbooks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from thesis2lib.archive_writer import archive_rows_for_student
from thesis2lib.excel_export import records_to_dataframe
from thesis2lib.individual_assessment import final_attempt_individual_data
from thesis2lib.models import IndividualAssessmentData, IndividualReaderEvaluation, ProcessedStudent
from thesis2lib.grading_scale import get_grading_scale
from thesis2lib.scoring import canvas_submission_raw_fields
from thesis2lib.utils import (
    format_grade_2dp,
    format_grade_display,
    normalize_reader_overall_grade,
    parse_score,
    sanitize_folder_name,
)


def final_attempt_label(final_attempt: str | None) -> str:
    if final_attempt == "First":
        return "1st submission"
    if final_attempt == "Second":
        return "2nd submission"
    return "Final submission"


def reader_evaluation_rows(evaluation: IndividualReaderEvaluation) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for criterion in evaluation.criteria:
        rows.append(
            {
                "Reader role": evaluation.reader_role,
                "Reader name": evaluation.reader_name,
                "Criterion": criterion.criterion,
                "Grade": format_grade_display(criterion.grade),
                "Motivation": criterion.comment,
            }
        )
    rows.append(
        {
            "Reader role": evaluation.reader_role,
            "Reader name": evaluation.reader_name,
            "Criterion": "Reader overall grade",
            "Grade": normalize_reader_overall_grade(evaluation.overall_grade),
            "Motivation": "",
        }
    )
    rows.append(
        {
            "Reader role": evaluation.reader_role,
            "Reader name": evaluation.reader_name,
            "Criterion": "Reader overall comment",
            "Grade": "",
            "Motivation": evaluation.overall_comment,
        }
    )
    return rows


def final_individual_assessment_rows(
    assessment: IndividualAssessmentData,
    submission_label: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for evaluation in (assessment.first_reader, assessment.second_reader):
        if not evaluation:
            continue
        for row in reader_evaluation_rows(evaluation):
            rows.append({"Submission": submission_label, **row})
    return rows


def final_reader_sheet_rows(
    assessment: IndividualAssessmentData,
    reader: IndividualReaderEvaluation | None,
) -> list[dict[str, str]]:
    if reader is None:
        return []
    submission_label = final_attempt_label(assessment.attempt)
    return [
        {"Submission": submission_label, **row} for row in reader_evaluation_rows(reader)
    ]


def _summary_row(
    *,
    grade_type: str,
    attempt_label: str,
    reader: str,
    score_fields: dict[str, str],
    canvas_grade: str = "",
    source: str,
) -> dict[str, str]:
    return {
        "Grade type": grade_type,
        "Submission": attempt_label,
        "Reader": reader,
        "Score": score_fields.get("Score", ""),
        "Out of": score_fields.get("Out of", ""),
        "Grade out of 10": score_fields.get("Grade out of 10", ""),
        "Canvas grade": canvas_grade,
        "Source": source,
    }


def grade_summary_rows(item: ProcessedStudent) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    attempt_label = final_attempt_label(item.final_attempt)
    student = item.student

    if student.first_assignment_score is not None:
        rows.append(
            _summary_row(
                grade_type="1st thesis assignment (Canvas)",
                attempt_label="1st submission",
                reader="Assignment",
                score_fields=canvas_submission_raw_fields(student.first_assignment_score),
                source="Canvas (1st assignment score)",
            )
        )
    if student.second_assignment_score is not None:
        rows.append(
            _summary_row(
                grade_type="2nd thesis assignment (Canvas)",
                attempt_label="2nd submission",
                reader="Assignment",
                score_fields=canvas_submission_raw_fields(student.second_assignment_score),
                source="Canvas (2nd assignment score)",
            )
        )
    from thesis2lib.scoring import format_course_final_grade_ten_scale

    course_final = format_course_final_grade_ten_scale(student)
    if course_final:
        rows.append(
            _summary_row(
                grade_type="Course final (latest assignment)",
                attempt_label=attempt_label,
                reader="—",
                score_fields={
                    "Score": "",
                    "Out of": "",
                    "Grade out of 10": course_final,
                },
                source="Latest of 1st/2nd assignment (not gradebook total)",
            )
        )

    final_individual = final_attempt_individual_data(
        item.final_attempt, item.individual_assessments
    )

    if final_individual:
        if final_individual.first_reader and final_individual.first_reader.overall_grade:
            raw = final_individual.first_reader.overall_grade
            fields = canvas_submission_raw_fields(parse_score(raw))
            if not fields["Score"]:
                fields = {"Score": normalize_reader_overall_grade(raw), "Out of": "", "Grade out of 10": ""}
            rows.append(
                _summary_row(
                    grade_type="Individual (overview)",
                    attempt_label=attempt_label,
                    reader="First reader",
                    score_fields=fields,
                    source=f"Overview ({final_individual.source_sheet})",
                )
            )
        if final_individual.second_reader and final_individual.second_reader.overall_grade:
            raw = final_individual.second_reader.overall_grade
            fields = canvas_submission_raw_fields(parse_score(raw))
            if not fields["Score"]:
                fields = {"Score": normalize_reader_overall_grade(raw), "Out of": "", "Grade out of 10": ""}
            rows.append(
                _summary_row(
                    grade_type="Individual (overview)",
                    attempt_label=attempt_label,
                    reader="Second reader",
                    score_fields=fields,
                    source=f"Overview ({final_individual.source_sheet})",
                )
            )

    if not final_individual:
        if item.first_reader_overall_grade:
            raw = item.first_reader_overall_grade
            fields = canvas_submission_raw_fields(parse_score(raw))
            if not fields["Score"]:
                fields = {"Score": normalize_reader_overall_grade(raw), "Out of": "", "Grade out of 10": ""}
            rows.append(
                _summary_row(
                    grade_type="Individual (overview)",
                    attempt_label=attempt_label,
                    reader="First reader",
                    score_fields=fields,
                    source="Overview",
                )
            )
        if item.second_reader_overall_grade:
            raw = item.second_reader_overall_grade
            fields = canvas_submission_raw_fields(parse_score(raw))
            if not fields["Score"]:
                fields = {"Score": normalize_reader_overall_grade(raw), "Out of": "", "Grade out of 10": ""}
            rows.append(
                _summary_row(
                    grade_type="Individual (overview)",
                    attempt_label=attempt_label,
                    reader="Second reader",
                    score_fields=fields,
                    source="Overview",
                )
            )

    joint = item.joint_assessment
    if joint:
        summary = joint.summary
        if summary.consolidated_score is not None or summary.consolidated_grade:
            rows.append(
                _summary_row(
                    grade_type="Joint (Canvas submission score)",
                    attempt_label=attempt_label,
                    reader="Supervisor",
                    score_fields=canvas_submission_raw_fields(summary.consolidated_score),
                    canvas_grade=summary.consolidated_grade,
                    source="Canvas (final submission)",
                )
            )
        if summary.rubric_points is not None:
            rows.append(
                _summary_row(
                    grade_type="Joint (Canvas rubric points)",
                    attempt_label=attempt_label,
                    reader="Supervisor",
                    score_fields={
                        "Score": format_grade_2dp(summary.rubric_points),
                        "Out of": format_grade_2dp(
                            get_grading_scale().submission_points_possible
                        ),
                        "Grade out of 10": "",
                    },
                    source="Canvas (final submission rubric)",
                )
            )

    return rows


def joint_assessment_rows(item: ProcessedStudent) -> list[dict[str, str]]:
    joint = item.joint_assessment
    if not joint:
        return []

    summary = joint.summary
    submission_fields = canvas_submission_raw_fields(summary.consolidated_score)
    scale = get_grading_scale()
    rows: list[dict[str, str]] = [
        {
            "Criterion": "Consolidated score (Canvas)",
            "Rating": summary.consolidated_grade,
            "Points": submission_fields["Score"],
            "Maximum points": submission_fields["Out of"] or format_grade_2dp(
                scale.submission_points_possible
            ),
            "Grade out of 10": submission_fields["Grade out of 10"],
            "Motivation": "",
            "Assessed at": summary.graded_at or summary.assessed_at,
        },
        {
            "Criterion": "Consolidated grade (Canvas)",
            "Rating": summary.consolidated_grade,
            "Points": "",
            "Maximum points": "",
            "Grade out of 10": "",
            "Motivation": "",
            "Assessed at": "",
        },
        {
            "Criterion": "Rubric points total",
            "Rating": "",
            "Points": "" if summary.rubric_points is None else format_grade_2dp(summary.rubric_points),
            "Maximum points": format_grade_2dp(scale.submission_points_possible),
            "Grade out of 10": "",
            "Motivation": "",
            "Assessed at": "",
        },
    ]
    for criterion in joint.criteria:
        rows.append(
            {
                "Criterion": criterion.criterion_description or criterion.criterion_id,
                "Rating": criterion.rating,
                "Points": "" if criterion.points is None else format_grade_2dp(criterion.points),
                "Maximum points": (
                    ""
                    if criterion.maximum_points is None
                    else format_grade_2dp(criterion.maximum_points)
                ),
                "Motivation": criterion.comments,
                "Assessed at": criterion.assessment_timestamp,
            }
        )
    return rows


def autosize_assessment_workbook(path: Path, sheet_names: list[str]) -> None:
    wb = load_workbook(path)
    for sheet_name in sheet_names:
        ws = wb[sheet_name]
        ws.freeze_panes = "A2"
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, column_cells in enumerate(ws.columns, start=1):
            longest = max(len(str(cell.value or "")) for cell in column_cells)
            ws.column_dimensions[get_column_letter(index)].width = min(
                max(longest + 2, 14), 60
            )
    wb.save(path)


def write_assessments_file(
    item: ProcessedStudent,
    student_folder: Path,
    student_id: str,
    overwrite: bool,
    destination: Path | None = None,
) -> Path | None:
    """Write the per-student assessment workbook for the final submission only."""
    final_individual = final_attempt_individual_data(
        item.final_attempt, item.individual_assessments
    )
    joint_rows = joint_assessment_rows(item)
    summary_rows = grade_summary_rows(item)
    attempt_label = final_attempt_label(item.final_attempt)

    is_delivery_output = destination is not None
    has_content = bool(
        final_individual
        or joint_rows
        or summary_rows
        or item.student.name
    )
    if not is_delivery_output and not has_content:
        return None

    target = destination or (
        student_folder / f"{sanitize_folder_name(student_id)}_Assessments.xlsx"
    )
    if target.exists() and not overwrite:
        return target

    sheet_names: list[str] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        archive_rows = archive_rows_for_student(item, thesis_title=item.thesis_title)
        if archive_rows:
            records_to_dataframe(archive_rows).to_excel(
                writer,
                sheet_name="Archive Information",
                index=False,
            )
            sheet_names.append("Archive Information")

        if summary_rows:
            records_to_dataframe(summary_rows).to_excel(
                writer,
                sheet_name="Grade Summary",
                index=False,
            )
            sheet_names.append("Grade Summary")

        if final_individual:
            combined = final_individual_assessment_rows(final_individual, attempt_label)
            if combined:
                records_to_dataframe(combined).to_excel(
                    writer,
                    sheet_name="Individual Assessment",
                    index=False,
                )
                sheet_names.append("Individual Assessment")

            first_reader_rows = final_reader_sheet_rows(
                final_individual, final_individual.first_reader
            )
            if first_reader_rows:
                records_to_dataframe(first_reader_rows).to_excel(
                    writer,
                    sheet_name="Individual - First Reader",
                    index=False,
                )
                sheet_names.append("Individual - First Reader")

            second_reader_rows = final_reader_sheet_rows(
                final_individual, final_individual.second_reader
            )
            if second_reader_rows:
                records_to_dataframe(second_reader_rows).to_excel(
                    writer,
                    sheet_name="Individual - Second Reader",
                    index=False,
                )
                sheet_names.append("Individual - Second Reader")

        if joint_rows:
            records_to_dataframe(joint_rows).to_excel(
                writer,
                sheet_name="Joint Assessment",
                index=False,
            )
            sheet_names.append("Joint Assessment")

        if not sheet_names:
            records_to_dataframe(
                [
                    {
                        "Note": (
                            "No individual or joint assessment data was found for "
                            "the student's final submission."
                        )
                    }
                ]
            ).to_excel(writer, sheet_name="Assessment", index=False)
            sheet_names.append("Assessment")

    autosize_assessment_workbook(target, sheet_names)
    return target
