"""Official score and course result rules."""

from __future__ import annotations

from typing import Any

from thesis2lib.grading_scale import get_grading_scale
from thesis2lib.models import StudentRecord
from thesis2lib.submissions import has_real_submission
from thesis2lib.utils import format_grade_2dp, parse_score, round_grade_2dp


def derive_final_score_from_submission(submission_score: float) -> float:
    """Convert a Canvas submission score to the gradebook Final Score scale."""
    return get_grading_scale().submission_to_gradebook(submission_score)


def derive_override_score_from_final(final_score: float) -> float:
    """Round Final Score on the gradebook scale (nearest 0.5 if 0–10, else nearest 5)."""
    scale = get_grading_scale()
    if scale.gradebook_points_possible <= 10.5:
        return round_grade_2dp(round(final_score * 2.0) / 2.0)
    return round_grade_2dp(round(final_score / 5.0) * 5)


def fill_missing_gradebook_scores(
    student: StudentRecord,
    submission_score: float,
) -> None:
    """Fill enrollment Final/Override when Canvas left them blank."""
    if student.final_score is None:
        student.final_score = derive_final_score_from_submission(submission_score)
    if student.override_score is None:
        base = student.final_score
        if base is None:
            base = derive_final_score_from_submission(submission_score)
        student.override_score = derive_override_score_from_final(base)


def resolve_final_score(student: StudentRecord) -> float | None:
    """Final Score on the configured gradebook scale."""
    if student.final_score is not None:
        return student.final_score
    if student.official_score is not None:
        return derive_final_score_from_submission(student.official_score)
    return None


def resolve_override_score(student: StudentRecord) -> float | None:
    """Override Score on the configured gradebook scale."""
    if student.override_score is not None:
        return student.override_score
    final_score = resolve_final_score(student)
    if final_score is not None:
        return derive_override_score_from_final(final_score)
    return None


def format_course_final_grade_ten_scale(student: StudentRecord) -> str:
    """Rounded course final grade on the configured course grade scale (usually 0–10)."""
    return get_grading_scale().gradebook_to_course_grade(resolve_override_score(student))


def format_student_final_score_course(student: StudentRecord) -> str:
    """Final Score expressed on the course grade scale (e.g. 0–10)."""
    return get_grading_scale().gradebook_to_course_grade(resolve_final_score(student))


def format_student_override_score_course(student: StudentRecord) -> str:
    """Override Score expressed on the course grade scale (e.g. 0–10)."""
    return get_grading_scale().gradebook_to_course_grade(resolve_override_score(student))


def format_submission_score_course(score: float | None) -> str:
    """Canvas submission score on the course grade scale (e.g. 5.5 from 33/60)."""
    if score is None:
        return ""
    return format_grade_2dp(get_grading_scale().submission_to_course_grade(score))


def gradebook_display_score(student: StudentRecord) -> float | None:
    """Best available score on the gradebook scale (for internal use)."""
    if student.override_score is not None:
        return student.override_score
    if student.final_score is not None:
        return student.final_score
    return student.official_score


def choose_last_submission_score(
    first_submission: dict | None,
    second_submission: dict | None,
) -> tuple[float | None, str]:
    """Use the latest submitted attempt's Canvas score (for programmes without gradebook totals)."""
    if has_real_submission(second_submission):
        second_score = parse_score(second_submission.get("score"))
        if second_score is not None:
            return second_score, "Second submission score"
    if has_real_submission(first_submission):
        first_score = parse_score(first_submission.get("score"))
        if first_score is not None:
            return first_score, "First submission score"
    return None, "None"


def apply_last_submission_grade(
    student: StudentRecord,
    first_submission: dict | None,
    second_submission: dict | None,
) -> bool:
    """Prefer the latest submission score for official_score when present."""
    submission_score, submission_source = choose_last_submission_score(
        first_submission,
        second_submission,
    )
    if submission_score is not None:
        student.official_score = round_grade_2dp(submission_score)
        student.official_score_source = submission_source
        return True
    return False


def resolve_course_grade_number(student: StudentRecord) -> float | None:
    """Course final grade on the course scale (usually 0–10), for pass/fail and display."""
    scale = get_grading_scale()

    override = resolve_override_score(student)
    if override is not None:
        return scale.gradebook_to_course_grade_number(override)

    final = resolve_final_score(student)
    if final is not None:
        return scale.gradebook_to_course_grade_number(final)

    if student.official_score is None:
        return None

    source = (student.official_score_source or "").casefold()
    if "submission score" in source:
        return scale.submission_to_course_grade(student.official_score)

    return scale.gradebook_to_course_grade_number(student.official_score)


def choose_official_score(
    override_raw: Any,
    final_raw: Any,
) -> tuple[float | None, str]:
    override = parse_score(override_raw)
    final = parse_score(final_raw)
    if override is not None:
        return override, "Override Score"
    if final is not None:
        return final, "Final Score"
    return None, "None"


def determine_course_result(
    student: StudentRecord,
    first_submission: dict | None,
    second_submission: dict | None,
) -> str:
    if (
        not has_real_submission(first_submission)
        and not has_real_submission(second_submission)
    ):
        return "No submission"

    course_grade = resolve_course_grade_number(student)
    if course_grade is None:
        return "Not assessed"

    scale = get_grading_scale()
    if course_grade >= scale.course_pass_grade:
        return "Passed"
    return "Failed"
