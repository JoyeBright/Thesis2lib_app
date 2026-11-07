"""Build structured records from Canvas API payloads."""

from __future__ import annotations

from thesis2lib.models import (
    JointAssessmentCriterion,
    JointAssessmentData,
    JointAssessmentSummary,
    StudentRecord,
    SubmissionCommentEntry,
    SubmissionRecord,
)
from thesis2lib.constants import (
    EXCLUDED_ENROLLMENT_ROLE_MARKERS,
    EXCLUDED_ENROLLMENT_TYPES,
    STUDENT_ENROLLMENT_TYPE,
)
from thesis2lib.scoring import choose_official_score
from thesis2lib.utils import clean_text, normalize_identifier, parse_score, round_grade_2dp

ENROLLMENT_STATE_PRIORITY = {
    "active": 4,
    "invited": 3,
    "completed": 2,
    "inactive": 1,
}


def enrollment_user_id(enrollment: dict) -> str:
    user = enrollment.get("user") or {}
    return normalize_identifier(user.get("id") or enrollment.get("user_id"))


def enrollment_quality(enrollment: dict) -> tuple[int, int, str]:
    state = clean_text(enrollment.get("enrollment_state")).casefold()
    state_score = ENROLLMENT_STATE_PRIORITY.get(state, 0)
    grades = enrollment.get("grades") or {}
    grade_score = (
        1
        if grades.get("final_score") is not None
        or grades.get("override_score") is not None
        else 0
    )
    updated = clean_text(
        enrollment.get("updated_at") or enrollment.get("created_at") or ""
    )
    return (state_score, grade_score, updated)


def is_student_enrollment(enrollment: dict) -> bool:
    enrollment_type = clean_text(enrollment.get("type"))
    if enrollment_type in EXCLUDED_ENROLLMENT_TYPES:
        return False

    role = clean_text(enrollment.get("role")).casefold()
    if role and any(marker in role for marker in EXCLUDED_ENROLLMENT_ROLE_MARKERS):
        return False

    if enrollment_type == STUDENT_ENROLLMENT_TYPE:
        return True
    if role in ("student", "studentenrollment"):
        return True
    return False


def filter_student_enrollments(enrollments: list[dict]) -> list[dict]:
    """Keep only Canvas student enrollments; drop teachers, TAs, designers, observers."""
    return [enrollment for enrollment in enrollments if is_student_enrollment(enrollment)]


def deduplicate_enrollments(enrollments: list[dict]) -> list[dict]:
    """Keep one enrollment per Canvas user (Canvas may return multiple sections/states)."""
    grouped: dict[str, list[dict]] = {}
    for enrollment in enrollments:
        user_id = enrollment_user_id(enrollment)
        if not user_id:
            continue
        grouped.setdefault(user_id, []).append(enrollment)

    return [max(items, key=enrollment_quality) for items in grouped.values()]


def build_submission_record(assignment_id: int, raw: dict) -> SubmissionRecord:
    user_id = normalize_identifier(raw.get("user_id"))
    return SubmissionRecord(
        assignment_id=assignment_id,
        student_canvas_id=user_id,
        score=parse_score(raw.get("score")),
        grade=clean_text(raw.get("grade")) or None,
        submitted_at=clean_text(raw.get("submitted_at")) or None,
        workflow_state=clean_text(raw.get("workflow_state")) or None,
        attempt=raw.get("attempt"),
        attachments=list(raw.get("attachments") or []),
        rubric_assessment=raw.get("rubric_assessment"),
        raw=raw,
    )


def submission_map(
    submissions: list[dict], assignment_id: int
) -> dict[str, SubmissionRecord]:
    mapping: dict[str, SubmissionRecord] = {}
    for raw in submissions:
        user_id = normalize_identifier(raw.get("user_id"))
        if user_id:
            mapping[user_id] = build_submission_record(assignment_id, raw)
    return mapping


def build_student_record(enrollment: dict) -> StudentRecord:
    user = enrollment.get("user") or {}
    grades = enrollment.get("grades") or {}
    canvas_id = normalize_identifier(user.get("id") or enrollment.get("user_id"))
    sis_user_id = normalize_identifier(user.get("sis_user_id"))
    name = clean_text(user.get("sortable_name") or user.get("name"))
    section = clean_text(
        enrollment.get("course_section_id") or enrollment.get("section")
    )

    final_score = parse_score(grades.get("final_score"))
    override_score = parse_score(grades.get("override_score"))
    if final_score is not None:
        final_score = round_grade_2dp(final_score)
    if override_score is not None:
        override_score = round_grade_2dp(override_score)
    official_score, official_source = choose_official_score(
        grades.get("override_score"),
        grades.get("final_score"),
    )
    if official_score is not None:
        official_score = round_grade_2dp(official_score)

    return StudentRecord(
        canvas_id=canvas_id,
        sis_user_id=sis_user_id,
        name=name,
        section=section,
        final_score=final_score,
        override_score=override_score,
        official_score=official_score,
        official_score_source=official_source,
        course_result="Not assessed",
    )


RUBRIC_META_KEYS = frozenset(
    {
        "score",
        "assessor_id",
        "assessed_at",
        "artifact_id",
        "artifact_type",
        "data",
        "ratings",
        "rating_id",
        "points",
        "comments",
        "description",
        "criterion_id",
        "id",
    }
)


def extract_comment_text(raw: dict) -> str:
    for key in ("comment", "comments", "comments_html", "text_comment"):
        value = clean_text(raw.get(key))
        if value:
            return value
    return ""


def rubric_criterion_entries(assessment: dict | None) -> list[tuple[str, dict]]:
    if not assessment or not isinstance(assessment, dict):
        return []

    data = assessment.get("data")
    if isinstance(data, list):
        entries: list[tuple[str, dict]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            criterion_id = clean_text(item.get("criterion_id") or item.get("id"))
            if criterion_id:
                entries.append((criterion_id, item))
        if entries:
            return entries

    entries = []
    for key, value in assessment.items():
        if key in RUBRIC_META_KEYS or not isinstance(value, dict):
            continue
        entries.append((clean_text(key), value))
    return entries


def rubric_rating_label(item: dict, rating_id: str) -> str:
    if not rating_id:
        return ""
    for rating in item.get("ratings") or []:
        if not isinstance(rating, dict):
            continue
        if clean_text(rating.get("id")) == rating_id:
            return clean_text(rating.get("description") or rating.get("long_description"))
    return ""


def build_joint_criterion(
    criterion_id: str,
    item: dict,
    assessment: dict,
) -> JointAssessmentCriterion:
    rating_id = clean_text(item.get("rating_id"))
    rating = clean_text(
        item.get("rating")
        or item.get("rating_description")
        or rubric_rating_label(item, rating_id)
    )
    assessor_id = normalize_identifier(
        assessment.get("assessor_id") or assessment.get("grader_id")
    )
    return JointAssessmentCriterion(
        criterion_id=criterion_id,
        criterion_description=clean_text(
            item.get("description") or item.get("criterion") or item.get("long_description")
        ),
        rating=rating,
        rating_id=rating_id,
        points=parse_score(item.get("points")),
        maximum_points=parse_score(
            item.get("points_possible") or item.get("criterion_points")
        ),
        comments=extract_comment_text(item),
        grader_id=assessor_id,
        grader_name="",
        assessment_timestamp=clean_text(
            assessment.get("assessed_at") or assessment.get("graded_at")
        ),
    )


def choose_rubric_payload(raw: dict | None) -> dict | None:
    if not raw:
        return None
    full = raw.get("full_rubric_assessment")
    if isinstance(full, dict) and rubric_criterion_entries(full):
        return full
    rubric = raw.get("rubric_assessment")
    if isinstance(rubric, dict):
        return rubric
    return None


def submission_has_rubric_grades(raw: dict | None) -> bool:
    """True when Canvas shows rubric criterion scores on this submission."""
    if not raw:
        return False
    assessment = choose_rubric_payload(raw)
    if assessment and rubric_criterion_entries(assessment):
        return True
    if assessment and parse_score(assessment.get("score")) is not None:
        return True
    return parse_score(raw.get("score")) is not None


def extract_submission_comments(
    submission: SubmissionRecord | None,
) -> list[SubmissionCommentEntry]:
    if not submission:
        return []

    comments: list[SubmissionCommentEntry] = []
    for raw in submission.raw.get("submission_comments") or []:
        if not isinstance(raw, dict):
            continue
        text = extract_comment_text(raw)
        if not text:
            continue
        comments.append(
            SubmissionCommentEntry(
                comment_id=normalize_identifier(raw.get("id")),
                author_id=normalize_identifier(raw.get("author_id")),
                author_name=clean_text(raw.get("author_name")),
                comment=text,
                created_at=clean_text(raw.get("created_at")),
                edited_at=clean_text(raw.get("edited_at")),
            )
        )
    return comments


def extract_joint_assessment(
    submission: SubmissionRecord | None,
) -> JointAssessmentData | None:
    if not submission:
        return None

    raw = submission.raw
    assessment = choose_rubric_payload(raw)
    criteria = [
        build_joint_criterion(criterion_id, item, assessment or {})
        for criterion_id, item in rubric_criterion_entries(assessment)
    ]

    rubric_points = parse_score((assessment or {}).get("score"))
    if rubric_points is None:
        point_values = [
            criterion.points for criterion in criteria if criterion.points is not None
        ]
        rubric_points = sum(point_values) if point_values else None

    summary = JointAssessmentSummary(
        consolidated_score=submission.score,
        consolidated_grade=clean_text(submission.grade),
        rubric_points=rubric_points,
        assessor_id=normalize_identifier(
            (assessment or {}).get("assessor_id") or raw.get("grader_id")
        ),
        assessed_at=clean_text((assessment or {}).get("assessed_at")),
        graded_at=clean_text(raw.get("graded_at")),
    )

    has_summary = any(
        (
            summary.consolidated_score is not None,
            summary.consolidated_grade,
            summary.rubric_points is not None,
            summary.assessor_id,
            summary.assessed_at,
            summary.graded_at,
        )
    )
    if not criteria and not has_summary:
        return None

    return JointAssessmentData(summary=summary, criteria=criteria)
