"""Extract individual reader assessments from overview grading sheets."""

from __future__ import annotations

from thesis2lib.constants import (
    GRADING_FIRST_SHEET,
    GRADING_SECOND_SHEET,
    INDIVIDUAL_READER_CRITERIA,
)
from thesis2lib.models import (
    IndividualAssessmentData,
    IndividualCriterionScore,
    IndividualReaderEvaluation,
    OverviewWorkbook,
)
from thesis2lib.overview_reader import lookup_grading_record
from thesis2lib.utils import clean_text


def reader_overall_grade(record: dict[str, str], prefix: str) -> str:
    if prefix == "FirstReader_":
        keys = ("RoundedFirst", "OverallGradeFirst")
    else:
        keys = ("RoundedSecond", "OverallGradeSecond")
    for key in keys:
        value = clean_text(record.get(key, ""))
        if value:
            return value
    return ""


def extract_reader_evaluation(
    record: dict[str, str],
    prefix: str,
    comments_key: str,
    reader_role: str,
    reader_name: str,
) -> IndividualReaderEvaluation | None:
    criteria: list[IndividualCriterionScore] = []
    for stem, label in INDIVIDUAL_READER_CRITERIA:
        grade = clean_text(record.get(f"{prefix}{stem}", ""))
        comment = clean_text(record.get(f"{prefix}{stem}_comm", ""))
        criteria.append(
            IndividualCriterionScore(criterion=label, grade=grade, comment=comment)
        )

    overall_grade = reader_overall_grade(record, prefix)
    overall_comment = clean_text(record.get(comments_key, ""))
    has_data = bool(overall_grade or overall_comment) or any(
        criterion.grade or criterion.comment for criterion in criteria
    )
    if not has_data:
        return None

    return IndividualReaderEvaluation(
        reader_role=reader_role,
        reader_name=reader_name,
        overall_grade=overall_grade,
        overall_comment=overall_comment,
        criteria=criteria,
    )


def extract_individual_assessments(
    record: dict[str, str] | None,
    attempt: str | None,
    supervisor_name: str = "",
    second_reader_name: str = "",
) -> IndividualAssessmentData | None:
    if not record or not attempt:
        return None

    source_sheet = GRADING_FIRST_SHEET if attempt == "First" else GRADING_SECOND_SHEET
    first_reader = extract_reader_evaluation(
        record,
        prefix="FirstReader_",
        comments_key="FirstReader_comments",
        reader_role="First reader",
        reader_name=supervisor_name,
    )
    second_reader = extract_reader_evaluation(
        record,
        prefix="SecondReader_",
        comments_key="SecondReader_comments",
        reader_role="Second reader",
        reader_name=second_reader_name,
    )

    if not first_reader and not second_reader:
        return None

    return IndividualAssessmentData(
        attempt=attempt,
        source_sheet=source_sheet,
        first_reader=first_reader,
        second_reader=second_reader,
    )


def extract_all_individual_assessments(
    overview: OverviewWorkbook,
    attempts: list[str],
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str = "",
    student_name: str = "",
) -> list[IndividualAssessmentData]:
    assessments: list[IndividualAssessmentData] = []
    for attempt in attempts:
        grading_record = lookup_grading_record(
            overview,
            attempt,
            sis_user_id,
            canvas_id,
            overview_snr,
            student_name,
        )
        supervisor_name = clean_text(
            grading_record.get("supervisor_name", "") if grading_record else ""
        )
        second_reader_name = clean_text(
            grading_record.get("second_reader_name", "") if grading_record else ""
        )
        data = extract_individual_assessments(
            grading_record,
            attempt,
            supervisor_name,
            second_reader_name,
        )
        if data:
            assessments.append(data)
    return assessments


def ensure_final_attempt_assessments(
    overview: OverviewWorkbook,
    assessments: list[IndividualAssessmentData],
    final_attempt: str | None,
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str,
    student_name: str,
) -> list[IndividualAssessmentData]:
    if not final_attempt:
        return assessments
    if any(item.attempt == final_attempt for item in assessments):
        return assessments

    grading_record = lookup_grading_record(
        overview,
        final_attempt,
        sis_user_id,
        canvas_id,
        overview_snr,
        student_name,
    )
    supervisor_name = clean_text(
        grading_record.get("supervisor_name", "") if grading_record else ""
    )
    second_reader_name = clean_text(
        grading_record.get("second_reader_name", "") if grading_record else ""
    )
    data = extract_individual_assessments(
        grading_record,
        final_attempt,
        supervisor_name,
        second_reader_name,
    )
    if data:
        assessments.append(data)
    return assessments


def individual_assessment_has_data(data: IndividualAssessmentData | None) -> bool:
    return data is not None


def final_attempt_individual_data(
    final_attempt: str | None,
    assessments: list[IndividualAssessmentData],
) -> IndividualAssessmentData | None:
    """Individual reader forms for the student's final submission only."""
    if not final_attempt:
        return None
    for assessment in assessments:
        if assessment.attempt == final_attempt:
            return assessment
    return None
