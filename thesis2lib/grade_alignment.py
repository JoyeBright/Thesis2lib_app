"""Compare Canvas joint grades with overview individual reader grades."""

from __future__ import annotations

from thesis2lib.grading_checks import NO_SUBMISSION_DETAIL, student_has_submission, unrounded_reader_average
from thesis2lib.models import ProcessedStudent
from thesis2lib.utils import parse_score

GRADE_TOLERANCE = 0.05

ALIGNMENT_ALIGNED = "Aligned"
ALIGNMENT_MISALIGNED = "Misaligned"
ALIGNMENT_NOT_COMPARABLE = "Not comparable"


def reader_grade_values(first_reader_grade: str, second_reader_grade: str) -> list[float]:
    values: list[float] = []
    for raw in (first_reader_grade, second_reader_grade):
        score = parse_score(raw)
        if score is not None:
            values.append(score)
    return values


def canvas_joint_grade_value(item: ProcessedStudent) -> float | None:
    joint = item.joint_assessment
    if not joint:
        return None
    summary = joint.summary
    for raw in (summary.consolidated_grade, summary.consolidated_score):
        score = parse_score(raw)
        if score is not None and score <= 10:
            return score
    return None


def values_match(left: float, right: float, tolerance: float = GRADE_TOLERANCE) -> bool:
    return abs(left - right) <= tolerance


def joint_individual_alignment(
    item: ProcessedStudent,
) -> tuple[str, str]:
    if not student_has_submission(item):
        return ALIGNMENT_NOT_COMPARABLE, NO_SUBMISSION_DETAIL

    if not item.joint_assessment_found:
        return ALIGNMENT_NOT_COMPARABLE, "Joint assessment missing on Canvas."
    if not item.first_reader_overall_grade and not item.second_reader_overall_grade:
        return ALIGNMENT_NOT_COMPARABLE, "Overview reader overall grades are missing."

    joint_value = canvas_joint_grade_value(item)
    if joint_value is None:
        return ALIGNMENT_NOT_COMPARABLE, "Canvas joint consolidated grade is missing."

    reader_values = reader_grade_values(
        item.first_reader_overall_grade,
        item.second_reader_overall_grade,
    )
    if not reader_values:
        return ALIGNMENT_NOT_COMPARABLE, "Overview reader overall grades are missing."

    for reader_value in reader_values:
        if values_match(joint_value, reader_value):
            return (
                ALIGNMENT_ALIGNED,
                f"Canvas joint grade matches a reader overall grade ({reader_value:g}).",
            )

    reader_average = unrounded_reader_average(
        item.first_reader_overall_grade,
        item.second_reader_overall_grade,
    )
    if reader_average is not None and values_match(joint_value, reader_average):
        return (
            ALIGNMENT_ALIGNED,
            f"Canvas joint grade matches the unrounded reader average ({reader_average:g}).",
        )

    average_text = f"{reader_average:g}" if reader_average is not None else "unknown"
    reader_text = ", ".join(str(value) for value in reader_values)
    return (
        ALIGNMENT_MISALIGNED,
        "Canvas joint grade "
        f"({joint_value:g}) does not match reader grades ({reader_text}) "
        f"or their unrounded average ({average_text}).",
    )
