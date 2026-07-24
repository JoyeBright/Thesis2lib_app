"""Shared constants for Thesis2Lib."""

from __future__ import annotations

import re

DEFAULT_PASS_THRESHOLD = 60.0
SUBMISSION_PASS_THRESHOLD = 30.0
SUBMISSION_SCORE_MAX = 60.0
INDIVIDUAL_CRITERION_PASS_THRESHOLD = 6.0
DEFAULT_PROCESSING_REPORT = "Processing_Report.xlsx"
DEFAULT_LOG_LEVEL = "INFO"

BLANK_MARKERS = {"", "--", "n/a", "na", "none", "null", "nan"}

STRONG_REVISION_TERMS = (
    "revision document",
    "revision form",
    "revision report",
    "revision overview",
    "response to feedback",
    "response to comments",
    "response to reviewers",
    "response letter",
    "response document",
    "list of changes",
    "changes made",
    "change log",
    "changelog",
    "resubmission form",
    "resubmission details",
    "summary of revisions",
    "statement of changes",
    "feedback explanations",
    "thesis feedback",
    "thesis-feedback",
    "info sheet",
    "infosheet",
    "rebuttal",
)

AMBIGUOUS_REVISION_TERMS = (
    "revision",
    "revisions",
    "revised",
)

INDIVIDUAL_READER_CRITERIA: tuple[tuple[str, str], ...] = (
    ("RQ", "Research question"),
    ("Literature", "Literature review"),
    ("Methods", "Research design / methods"),
    ("Results", "Results"),
    ("Discussion", "Discussion"),
    ("FormPres", "Form and presentation"),
)

ASSESSMENT_SHEET_ALIASES = {
    "First grading": "First grading",
    "Second grading": "Second grading",
}

ASSESSMENT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "student_id": ("student id", "sis user id", "sis id", "student number"),
    "canvas_id": ("canvas id", "id", "canvas user id"),
    "student_name": ("student", "student name", "name"),
    "assessor_role": ("assessor role", "role", "reader role"),
    "assessor_name": ("assessor name", "assessor", "reader", "supervisor"),
    "criterion_id": ("criterion id", "criterion number"),
    "criterion": ("criterion", "criterion name", "criterion description"),
    "score": ("score", "points", "grade"),
    "maximum_score": ("maximum score", "max score", "points possible", "max"),
    "motivation": ("motivation", "comments", "comment", "feedback"),
    "submitted_timestamp": (
        "submitted timestamp",
        "submitted at",
        "submission date",
        "timestamp",
        "date",
    ),
}

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
SENSITIVE_LOG_PATTERN = re.compile(
    r"(authorization|bearer|token|api_key|password|secret|cookie)",
    re.IGNORECASE,
)
CANVAS_BULK_PREFIX = re.compile(r"^[\w.]+\s+\d+\s+\d+\s+")
SUBMISSION_INCLUDES = (
    "user",
    "submission_history",
    "submission_comments",
    "rubric_assessment",
    "full_rubric_assessment",
)
ENROLLMENT_INCLUDES = ("user", "total_scores")

STUDENT_ENROLLMENT_TYPE = "StudentEnrollment"
EXCLUDED_ENROLLMENT_TYPES = frozenset(
    {
        "TeacherEnrollment",
        "TaEnrollment",
        "DesignerEnrollment",
        "ObserverEnrollment",
    }
)
EXCLUDED_ENROLLMENT_ROLE_MARKERS = (
    "teacher",
    "ta",
    "designer",
    "observer",
    "admin",
    "staff",
    "coordinator",
)

GRADING_FIRST_SHEET = "Grading_1st"
GRADING_SECOND_SHEET = "Grading_2nd"

MATCHING_SHEET_PREFIX = "Matching_"

OVERVIEW_IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "canvas_id": ("id", "canvas id"),
    "sis_user_id": ("sis user id", "sis id", "student id", "snr"),
    "sis_login_id": ("sis login id", "login id"),
    "section": ("section",),
    "student_name": ("student", "student name", "name"),
    "student_first_name": ("studentfirstname", "student first name"),
    "student_last_name": ("studentlastname", "student last name"),
}

# Direct column names used in programme overview workbooks (e.g. DSS Overview).
DIRECT_GRADING_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "First": {
        "supervisor_name": ("supervisorcode", "supervisor"),
        "second_reader_name": ("secondreader", "second reader"),
        "final_grade": ("overallgradefirst", "roundedfirst"),
    },
    "Second": {
        "supervisor_name": ("supervisorcode", "supervisor"),
        "second_reader_name": ("secondreader", "second reader"),
        "final_grade": ("overallgradesecond", "roundedsecond"),
    },
}

GRADING_FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "supervisor_name": (
        "supervisor",
        "first-reader",
        "first reader",
        "supervisor / first-reader",
    ),
    "second_reader_name": ("second-reader", "second reader"),
    "third_reader_name": ("third reader",),
    "submission_date": ("submission date",),
    "final_grade": ("final grade",),
    "thesis_title": ("thesis title",),
}
