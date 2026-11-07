"""Data models for Thesis2Lib."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StudentRecord:
    canvas_id: str
    sis_user_id: str
    name: str
    section: str
    final_score: float | None
    override_score: float | None
    official_score: float | None
    official_score_source: str
    course_result: str
    first_assignment_score: float | None = None
    second_assignment_score: float | None = None


@dataclass
class SubmissionRecord:
    assignment_id: int
    student_canvas_id: str
    score: float | None
    grade: str | None
    submitted_at: str | None
    workflow_state: str | None
    attempt: int | None
    attachments: list[dict]
    rubric_assessment: dict | None
    raw: dict = field(repr=False, default_factory=dict)


@dataclass
class PdfSelection:
    status: str
    selected_path: Path | None
    candidates: list[Path]
    revision_documents: list[Path]
    matching_method: str
    confidence: str
    detail: str
    canvas_attachment_names: list[str] = field(default_factory=list)
    selected_attachment: dict | None = None
    candidate_attachments: list[dict] = field(default_factory=list)
    pdf_source: str = "local"
    needs_review: bool = False
    content_confidence: str = ""
    content_detail: str = ""
    single_pdf_second_submission: bool = False
    single_pdf_final_submission: bool = False


@dataclass
class AssessmentRow:
    student_id: str
    canvas_id: str
    assessor_role: str
    assessor_name: str
    criterion_id: str
    criterion: str
    score: float | None
    maximum_score: float | None
    motivation: str
    submitted_timestamp: str


@dataclass
class JointAssessmentCriterion:
    criterion_id: str
    criterion_description: str
    rating: str
    rating_id: str
    points: float | None
    maximum_points: float | None
    comments: str
    grader_id: str
    grader_name: str
    assessment_timestamp: str


@dataclass
class JointAssessmentSummary:
    consolidated_score: float | None
    consolidated_grade: str
    rubric_points: float | None
    assessor_id: str
    assessed_at: str
    graded_at: str


@dataclass
class JointAssessmentData:
    summary: JointAssessmentSummary
    criteria: list[JointAssessmentCriterion]


@dataclass
class SubmissionCommentEntry:
    comment_id: str
    author_id: str
    author_name: str
    comment: str
    created_at: str
    edited_at: str


@dataclass
class IndividualCriterionScore:
    criterion: str
    grade: str
    comment: str


@dataclass
class IndividualReaderEvaluation:
    reader_role: str
    reader_name: str
    overall_grade: str
    overall_comment: str
    criteria: list[IndividualCriterionScore]


@dataclass
class IndividualAssessmentData:
    attempt: str
    source_sheet: str
    first_reader: IndividualReaderEvaluation | None
    second_reader: IndividualReaderEvaluation | None


@dataclass
class ArchiveMetadata:
    student_name: str
    canvas_id: str
    sis_user_id: str
    sis_login_id: str
    section: str
    supervisor_name: str
    second_reader_name: str
    third_reader_name: str
    submission_date: str
    final_grade: str
    thesis_title: str
    source_sheet: str
    found: bool = False
    complete: bool = False
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class OverviewWorkbook:
    matching_sheet: str
    grading_first_by_sis: dict[str, dict[str, str]]
    grading_first_by_canvas: dict[str, dict[str, str]]
    grading_second_by_sis: dict[str, dict[str, str]]
    grading_second_by_canvas: dict[str, dict[str, str]]
    matching_by_sis: dict[str, dict[str, str]]
    matching_by_canvas: dict[str, dict[str, str]]


@dataclass
class ProcessedStudent:
    student: StudentRecord
    first_submission: SubmissionRecord | None
    second_submission: SubmissionRecord | None
    final_attempt: str | None
    pdf_selection: PdfSelection | None
    individual_assessment_found: bool
    supervisor_name_found: bool
    second_reader_name_found: bool
    joint_assessment_found: bool
    joint_assessment: JointAssessmentData | None
    submission_comments: list[SubmissionCommentEntry]
    individual_assessments: list[IndividualAssessmentData]
    archive_metadata: ArchiveMetadata | None
    package_status: str
    details: str
    first_reader_assessment_found: bool = False
    second_reader_assessment_found: bool = False
    overview_snr: str = ""
    overview_student_name: str = ""
    individual_overall_grade: str = ""
    first_reader_overall_grade: str = ""
    second_reader_overall_grade: str = ""
    individual_criterion_scores: int = 0
    joint_rubric_criteria: int = 0
    joint_rubric_points: float | None = None
    thesis_title: str = ""
    thesis_title_confidence: str = ""
    thesis_title_method: str = ""
    thesis_title_detail: str = ""
    thesis_title_needs_review: bool = False
