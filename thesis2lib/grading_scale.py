"""Per-course grading scales (submission, gradebook, and library 0–10 display)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from thesis2lib.constants import DEFAULT_COURSE_PASS_GRADE, SUBMISSION_SCORE_MAX
from thesis2lib.utils import format_grade_2dp, parse_score, round_grade_2dp

LOGGER = logging.getLogger(__name__)

DEFAULT_GRADEBOOK_MAX = 100.0
DEFAULT_COURSE_GRADE_MAX = 10.0

_ACTIVE: "GradingScale | None" = None


@dataclass(frozen=True)
class GradingScale:
    """How raw Canvas scores map to gradebook and course-grade (usually 0–10) scales."""

    submission_points_possible: float = SUBMISSION_SCORE_MAX
    gradebook_points_possible: float = DEFAULT_GRADEBOOK_MAX
    course_grade_points_possible: float = DEFAULT_COURSE_GRADE_MAX
    course_pass_grade: float = DEFAULT_COURSE_PASS_GRADE

    def __post_init__(self) -> None:
        if self.submission_points_possible <= 0:
            raise ValueError("submission_points_possible must be positive.")
        if self.gradebook_points_possible <= 0:
            raise ValueError("gradebook_points_possible must be positive.")
        if self.course_grade_points_possible <= 0:
            raise ValueError("course_grade_points_possible must be positive.")
        if self.course_pass_grade < 0:
            raise ValueError("course_pass_grade must be non-negative.")

    @classmethod
    def default(cls) -> GradingScale:
        return cls()

    def submission_to_gradebook(self, submission_score: float) -> float:
        """Convert a submission assignment score to the gradebook scale."""
        return round_grade_2dp(
            submission_score
            / self.submission_points_possible
            * self.gradebook_points_possible
        )

    def submission_to_course_grade(self, submission_score: float) -> float:
        return round_grade_2dp(
            submission_score
            / self.submission_points_possible
            * self.course_grade_points_possible
        )

    def gradebook_to_course_grade_number(self, gradebook_score: float | None) -> float | None:
        if gradebook_score is None:
            return None
        return round_grade_2dp(
            gradebook_score
            / self.gradebook_points_possible
            * self.course_grade_points_possible
        )

    def gradebook_to_course_grade(self, gradebook_score: float | None) -> str:
        number = self.gradebook_to_course_grade_number(gradebook_score)
        if number is None:
            return ""
        return format_grade_2dp(number)

    def submission_pass_points(self) -> float:
        """Minimum submission points that correspond to course_pass_grade."""
        return round_grade_2dp(
            self.course_pass_grade
            / self.course_grade_points_possible
            * self.submission_points_possible
        )

    def pass_threshold_for_raw_score(self, score: float) -> float:
        """Pass cut-off on the same scale as score (0–10, 0–60, or gradebook)."""
        if score <= self.course_grade_points_possible + 0.01:
            return self.course_pass_grade
        if score <= self.submission_points_possible + 0.01:
            return self.submission_pass_points()
        return round_grade_2dp(
            self.course_pass_grade
            / self.course_grade_points_possible
            * self.gradebook_points_possible
        )

    def raw_score_passes(self, score: float) -> bool:
        return score >= self.pass_threshold_for_raw_score(score)

    def cap_submission_score(self, score: float) -> float:
        return min(max(score, 0.0), self.submission_points_possible)


def get_grading_scale() -> GradingScale:
    return _ACTIVE or GradingScale.default()


def set_grading_scale(scale: GradingScale) -> None:
    global _ACTIVE
    _ACTIVE = scale


def _parse_positive_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _assignment_points_possible(client: Any, course_id: int, assignment_id: int) -> float | None:
    payload = client.get_assignment(course_id, assignment_id)
    return _parse_positive_float(payload.get("points_possible"))


def resolve_submission_points_possible(
    client: Any | None,
    course_id: int | None,
    first_assignment_id: int | None,
    second_assignment_id: int | None,
    config_value: Any,
) -> tuple[float, str]:
    """Return (max_points, source_description)."""
    configured = _parse_positive_float(config_value)
    if configured is not None:
        return configured, "config submission_score_max"

    if client is None or course_id is None:
        return SUBMISSION_SCORE_MAX, f"default ({SUBMISSION_SCORE_MAX:g})"

    for label, assignment_id in (
        ("second submission assignment", second_assignment_id),
        ("first submission assignment", first_assignment_id),
    ):
        if assignment_id is None:
            continue
        try:
            points = _assignment_points_possible(client, course_id, int(assignment_id))
        except Exception as exc:
            LOGGER.warning(
                "Could not read points_possible for %s %s: %s",
                label,
                assignment_id,
                exc,
            )
            continue
        if points is not None:
            return points, f"Canvas {label} (id {assignment_id})"

    return SUBMISSION_SCORE_MAX, f"default ({SUBMISSION_SCORE_MAX:g})"


def scale_from_peak(peak: float) -> float:
    """Map the highest observed score to a 10, 60, or 100 point scale."""
    if peak <= 10.5:
        return 10.0
    if peak <= 60.5:
        return 60.0
    return 100.0


def _collect_score_peak(scores: list[float | None]) -> tuple[float | None, int]:
    values = [score for score in scores if score is not None]
    if not values:
        return None, 0
    return max(values), len(values)


def infer_gradebook_points_from_enrollments(
    enrollments: list[dict],
) -> tuple[float | None, str]:
    """Guess gradebook scale from Canvas Final/Override scores."""
    scores: list[float | None] = []
    for enrollment in enrollments:
        grades = enrollment.get("grades") or {}
        for key in ("override_score", "final_score", "current_score"):
            scores.append(parse_score(grades.get(key)))
    peak, count = _collect_score_peak(scores)
    if peak is None:
        return None, "no gradebook scores to infer from"
    scale = scale_from_peak(peak)
    return scale, f"inferred from gradebook scores (max seen {peak:g})"


def infer_gradebook_points_from_submissions(
    submission_rows: list[dict],
) -> tuple[float | None, str]:
    """Guess gradebook scale from submission assignment scores."""
    scores: list[float | None] = []
    for raw in submission_rows:
        scores.append(parse_score(raw.get("score")))
    peak, count = _collect_score_peak(scores)
    if peak is None:
        return None, "no submission scores to infer from"
    scale = scale_from_peak(peak)
    return scale, f"inferred from submission scores (max seen {peak:g})"


def gradebook_default_from_submission_max(submission_points: float) -> tuple[float | None, str]:
    """When Canvas stores course totals on the same scale as the thesis assignment."""
    for candidate in (10.0, 60.0, 100.0):
        if abs(submission_points - candidate) < 0.01:
            return candidate, f"matched thesis assignment max ({candidate:g})"
    return None, ""


def resolve_gradebook_points_possible(
    *,
    config_value: Any,
    enrollments: list[dict] | None,
    submission_rows: list[dict] | None,
    submission_points: float,
) -> tuple[float, str]:
    configured = _parse_positive_float(config_value)
    if configured is not None:
        return configured, "config gradebook_score_max"

    from_enrollments, enroll_source = infer_gradebook_points_from_enrollments(
        enrollments or []
    )
    from_submissions, submission_source = infer_gradebook_points_from_submissions(
        submission_rows or []
    )

    if from_enrollments is not None and from_submissions is not None:
        if from_enrollments == from_submissions:
            return from_enrollments, enroll_source
        # Prefer gradebook enrolment totals when they disagree with raw submission peaks.
        LOGGER.info(
            "Gradebook scale %g from enrolments vs %g from submissions; using enrolments.",
            from_enrollments,
            from_submissions,
        )
        return from_enrollments, enroll_source

    if from_enrollments is not None:
        return from_enrollments, enroll_source
    if from_submissions is not None:
        return from_submissions, submission_source

    aligned, aligned_source = gradebook_default_from_submission_max(submission_points)
    if aligned is not None:
        return aligned, aligned_source

    return DEFAULT_GRADEBOOK_MAX, f"default ({DEFAULT_GRADEBOOK_MAX:g})"


def resolve_course_pass_grade(
    course_pass_grade: Any,
    pass_threshold: Any,
    *,
    gradebook_points_possible: float,
    course_grade_points_possible: float,
) -> float:
    configured = _parse_positive_float(course_pass_grade)
    if configured is not None:
        return round_grade_2dp(configured)

    legacy = _parse_positive_float(pass_threshold)
    if legacy is None:
        return DEFAULT_COURSE_PASS_GRADE
    if legacy <= course_grade_points_possible + 0.01:
        return round_grade_2dp(legacy)
    return round_grade_2dp(
        legacy / gradebook_points_possible * course_grade_points_possible
    )


def build_grading_scale(
    *,
    submission_score_max: Any = None,
    gradebook_score_max: Any = None,
    course_grade_max: Any = None,
    course_pass_grade: Any = None,
    pass_threshold: Any = None,
    client: Any | None = None,
    course_id: int | None = None,
    first_assignment_id: int | None = None,
    second_assignment_id: int | None = None,
    enrollments: list[dict] | None = None,
    submission_rows: list[dict] | None = None,
) -> GradingScale:
    submission_points, submission_source = resolve_submission_points_possible(
        client,
        course_id,
        first_assignment_id,
        second_assignment_id,
        submission_score_max,
    )

    gradebook_points, gradebook_source = resolve_gradebook_points_possible(
        config_value=gradebook_score_max,
        enrollments=enrollments,
        submission_rows=submission_rows,
        submission_points=submission_points,
    )

    course_points = _parse_positive_float(course_grade_max) or DEFAULT_COURSE_GRADE_MAX
    pass_grade = resolve_course_pass_grade(
        course_pass_grade,
        pass_threshold,
        gradebook_points_possible=gradebook_points,
        course_grade_points_possible=course_points,
    )

    scale_preview = GradingScale(
        submission_points_possible=submission_points,
        gradebook_points_possible=gradebook_points,
        course_grade_points_possible=course_points,
        course_pass_grade=pass_grade,
    )
    LOGGER.info(
        "Grading scale — submission 0–%g (%s), gradebook 0–%g (%s), "
        "course grade 0–%g, pass >= %g (>= %g on submission, >= %g on gradebook).",
        submission_points,
        submission_source,
        gradebook_points,
        gradebook_source,
        course_points,
        pass_grade,
        scale_preview.submission_pass_points(),
        scale_preview.pass_threshold_for_raw_score(gradebook_points),
    )

    return GradingScale(
        submission_points_possible=submission_points,
        gradebook_points_possible=gradebook_points,
        course_grade_points_possible=course_points,
        course_pass_grade=pass_grade,
    )


def configure_grading_scale(
    *,
    submission_score_max: Any = None,
    gradebook_score_max: Any = None,
    course_grade_max: Any = None,
    course_pass_grade: Any = None,
    pass_threshold: Any = None,
    client: Any | None = None,
    course_id: int | None = None,
    first_assignment_id: int | None = None,
    second_assignment_id: int | None = None,
    enrollments: list[dict] | None = None,
    submission_rows: list[dict] | None = None,
) -> GradingScale:
    scale = build_grading_scale(
        submission_score_max=submission_score_max,
        gradebook_score_max=gradebook_score_max,
        course_grade_max=course_grade_max,
        course_pass_grade=course_pass_grade,
        pass_threshold=pass_threshold,
        client=client,
        course_id=course_id,
        first_assignment_id=first_assignment_id,
        second_assignment_id=second_assignment_id,
        enrollments=enrollments,
        submission_rows=submission_rows,
    )
    set_grading_scale(scale)
    return scale
