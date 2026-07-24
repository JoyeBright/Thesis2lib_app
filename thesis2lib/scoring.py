"""Official score and course result rules."""

from __future__ import annotations

from typing import Any

from thesis2lib.constants import SUBMISSION_PASS_THRESHOLD, SUBMISSION_SCORE_MAX
from thesis2lib.models import StudentRecord
from thesis2lib.submissions import has_real_submission
from thesis2lib.utils import parse_score, round_grade_2dp


def derive_final_score_from_submission(submission_score: float) -> float:
    """Convert a 0–60 Canvas submission score to a 0–100 Final Score."""
    return round_grade_2dp(submission_score / SUBMISSION_SCORE_MAX * 100.0)


def derive_override_score_from_final(final_score: float) -> float:
    """Round a percentage Final Score to the nearest 5 (course Override Score)."""
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
    """Final Score on the 0–100 gradebook scale."""
    if student.final_score is not None:
        return student.final_score
    if student.official_score is not None:
        return derive_final_score_from_submission(student.official_score)
    return None


def resolve_override_score(student: StudentRecord) -> float | None:
    """Override Score on the 0–100 gradebook scale (rounded course grade)."""
    if student.override_score is not None:
        return student.override_score
    final_score = resolve_final_score(student)
    if final_score is not None:
        return derive_override_score_from_final(final_score)
    return None


def format_course_final_grade_ten_scale(student: StudentRecord) -> str:
    """Rounded course final grade on the 0–10 scale."""
    from thesis2lib.utils import format_gradebook_score_ten_scale

    return format_gradebook_score_ten_scale(resolve_override_score(student))


def gradebook_display_score(student: StudentRecord) -> float | None:
    """Score on the 0–100 scale for archive/report grade display."""
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
    pass_threshold: float,
) -> tuple[float, bool]:
    """Prefer the latest submission score; fall back to gradebook for pass/fail."""
    submission_score, submission_source = choose_last_submission_score(
        first_submission,
        second_submission,
    )
    if submission_score is not None:
        student.official_score = round_grade_2dp(submission_score)
        student.official_score_source = submission_source
        return SUBMISSION_PASS_THRESHOLD, True
    return pass_threshold, False


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
    official_score: float | None,
    first_submission: dict | None,
    second_submission: dict | None,
    pass_threshold: float,
    *,
    pass_above_threshold: bool = False,
) -> str:
    if (
        not has_real_submission(first_submission)
        and not has_real_submission(second_submission)
    ):
        return "No submission"
    if official_score is None:
        return "Not assessed"
    if pass_above_threshold:
        passed = official_score > pass_threshold
    else:
        passed = official_score >= pass_threshold
    if passed:
        return "Passed"
    return "Failed"
