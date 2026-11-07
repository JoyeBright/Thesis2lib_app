"""Individual and joint grading checks for the processing report."""

from __future__ import annotations

from thesis2lib.grading_scale import get_grading_scale
from thesis2lib.models import IndividualAssessmentData, IndividualReaderEvaluation, ProcessedStudent
from thesis2lib.utils import parse_score

PASS_LABEL = "Pass"
FAIL_LABEL = "Fail"
NOT_APPLICABLE = "Not applicable"
NO_SUBMISSION_DETAIL = "Student has not submitted."


def student_has_submission(item: ProcessedStudent) -> bool:
    return item.final_attempt is not None


def final_attempt_assessment(item: ProcessedStudent) -> IndividualAssessmentData | None:
    if not item.final_attempt or not item.individual_assessments:
        return None
    for assessment in item.individual_assessments:
        if assessment.attempt == item.final_attempt:
            return assessment
    return item.individual_assessments[-1]


def reader_assessment_found(item: ProcessedStudent) -> tuple[bool, bool]:
    assessment = final_attempt_assessment(item)
    if assessment is None:
        return False, False
    return assessment.first_reader is not None, assessment.second_reader is not None


def reader_criterion_scores(evaluation: IndividualReaderEvaluation | None) -> list[float]:
    if evaluation is None:
        return []
    scores: list[float] = []
    for criterion in evaluation.criteria:
        score = parse_score(criterion.grade)
        if score is not None:
            scores.append(score)
    return scores


def reader_overall_grade_value(raw_grade: str) -> float | None:
    return parse_score(raw_grade)


def unrounded_reader_average(first_grade: str, second_grade: str) -> float | None:
    values: list[float] = []
    for raw in (first_grade, second_grade):
        score = parse_score(raw)
        if score is not None:
            values.append(score)
    if not values:
        return None
    return sum(values) / len(values)


def individual_criteria_status(item: ProcessedStudent) -> tuple[str, str]:
    if not student_has_submission(item):
        return NOT_APPLICABLE, NO_SUBMISSION_DETAIL

    assessment = final_attempt_assessment(item)
    if assessment is None:
        return (
            NOT_APPLICABLE,
            "Individual reader assessment not found in overview for the final attempt.",
        )

    failing: list[str] = []
    for label, evaluation in (
        ("First reader", assessment.first_reader),
        ("Second reader", assessment.second_reader),
    ):
        if evaluation is None:
            continue
        scale = get_grading_scale()
        for score in reader_criterion_scores(evaluation):
            if not scale.raw_score_passes(score):
                failing.append(f"{label} criterion score {score:g}")

    if not failing and not any(
        reader_criterion_scores(evaluation)
        for evaluation in (assessment.first_reader, assessment.second_reader)
    ):
        return NOT_APPLICABLE, "Individual criterion scores are missing in the overview."

    if failing:
        return FAIL_LABEL, "; ".join(failing)

    scale = get_grading_scale()
    return (
        PASS_LABEL,
        f"All scored criteria meet the pass rule (course pass >= {scale.course_pass_grade:g}).",
    )


def joint_grade_value(item: ProcessedStudent) -> float | None:
    joint = item.joint_assessment
    if joint is None:
        return None
    summary = joint.summary
    for raw in (summary.consolidated_grade, summary.consolidated_score):
        score = parse_score(raw)
        if score is not None:
            return score
    return None


def joint_grade_status(item: ProcessedStudent) -> tuple[str, str]:
    if not student_has_submission(item):
        return NOT_APPLICABLE, NO_SUBMISSION_DETAIL

    if not item.joint_assessment_found:
        return NOT_APPLICABLE, "Joint assessment missing on Canvas."

    score = joint_grade_value(item)
    if score is None:
        return NOT_APPLICABLE, "Joint consolidated grade is missing."

    scale = get_grading_scale()
    if not scale.raw_score_passes(score):
        threshold = scale.pass_threshold_for_raw_score(score)
        return (
            FAIL_LABEL,
            f"Joint consolidated grade {score:g} is below pass ({threshold:g} on this scale).",
        )

    return PASS_LABEL, f"Joint consolidated grade {score:g} meets the pass rule."
