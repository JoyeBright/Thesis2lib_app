"""Student processing pipeline."""

from __future__ import annotations

from pathlib import Path

from thesis2lib.assessment_reader import AssessmentRow, lookup_individual_assessment
from thesis2lib.canvas_records import (
    build_student_record,
    extract_joint_assessment,
    extract_submission_comments,
)
from thesis2lib.canvas_attachments import (
    select_library_canvas_attachments,
    thesis_pdf_selected,
)
from thesis2lib.individual_assessment import (
    ensure_final_attempt_assessments,
    extract_all_individual_assessments,
)
from thesis2lib.models import (
    ArchiveMetadata,
    IndividualAssessmentData,
    OverviewWorkbook,
    PdfSelection,
    ProcessedStudent,
    SubmissionRecord,
)
from thesis2lib.overview_reader import (
    enrich_archive_metadata,
    lookup_archive_metadata,
    lookup_grading_record,
    lookup_overview_student_name,
    overview_snr_from_pdf_selection,
    resolve_overview_snr,
    reader_overall_grades,
    summarize_individual_grades,
)
from thesis2lib.submissions import has_real_submission
from thesis2lib.pdf_matching import select_library_pdf_candidate
from thesis2lib.scoring import determine_course_result
from thesis2lib.submissions import choose_final_attempt
from thesis2lib.progress import ConsoleProgress
from thesis2lib.utils import clean_text


def determine_package_status(
    course_result: str,
    final_attempt: str | None,
    pdf_selection: PdfSelection | None,
    individual_assessment_found: bool,
    joint_assessment_found: bool,
    assessment_workbook_provided: bool,
    overview_workbook_provided: bool,
    archive_metadata: ArchiveMetadata | None,
) -> tuple[str, str]:
    if course_result == "No submission":
        return "No submission", "No Canvas submissions and official score is 0."
    if course_result == "Not assessed":
        return "Not assessed", "Official score is missing."
    if course_result == "Failed":
        return "Failed", "Official score is below the pass threshold."
    if course_result != "Passed":
        return course_result, ""

    if not final_attempt:
        return "Manual Review", "Student passed but no real submission attempt was found."

    if pdf_selection is None:
        return "Manual Review", "PDF matching was not performed."

    if pdf_selection.status == "Missing PDF":
        return "Missing PDF", pdf_selection.detail
    if pdf_selection.status == "Multiple PDFs":
        return "Multiple PDFs", pdf_selection.detail
    if pdf_selection.status == "Manual Review":
        return "Manual Review", pdf_selection.detail
    if pdf_selection.status != "Matched" or not thesis_pdf_selected(pdf_selection):
        return "Manual Review", pdf_selection.detail

    details: list[str] = [pdf_selection.detail]
    missing_flags: list[str] = []

    if not individual_assessment_found:
        if assessment_workbook_provided:
            missing_flags.append("Missing assessment")
            details.append("Individual assessment workbook data was not found.")
        elif overview_workbook_provided:
            missing_flags.append("Missing assessment")
            details.append(
                "Individual assessment data was not found in the overview workbook."
            )

    if overview_workbook_provided:
        if archive_metadata is None or not archive_metadata.found:
            missing_flags.append("Missing archive info")
            details.append("Overview workbook row was not found for the final attempt.")
        elif not archive_metadata.complete:
            missing_flags.append("Incomplete archive info")
            details.append(
                "Overview archive fields are incomplete: "
                + ", ".join(archive_metadata.missing_fields)
                + "."
            )

    if not joint_assessment_found:
        missing_flags.append("Missing joint assessment")
        details.append("Joint Canvas rubric assessment is missing.")

    if not missing_flags:
        return "Ready", " ".join(details)

    return missing_flags[0], " ".join(details)


def refresh_student_overview_data(
    item: ProcessedStudent,
    overview_workbook: OverviewWorkbook | None,
    overview_snr: str,
) -> None:
    """Re-link a student to overview grading/archive rows after SNR is corrected."""
    if overview_workbook is None or not item.final_attempt:
        return

    from thesis2lib.utils import normalize_identifier

    student = item.student
    first = item.first_submission
    second = item.second_submission
    snr = normalize_identifier(overview_snr)
    item.overview_snr = snr

    assessment_attempts: list[str] = []
    if has_real_submission(first.raw if first else None):
        assessment_attempts.append("First")
    if has_real_submission(second.raw if second else None):
        assessment_attempts.append("Second")

    individual_assessments: list[IndividualAssessmentData] = []
    if assessment_attempts:
        individual_assessments = extract_all_individual_assessments(
            overview_workbook,
            assessment_attempts,
            student.sis_user_id,
            student.canvas_id,
            snr,
            student.name,
        )
        individual_assessments = ensure_final_attempt_assessments(
            overview_workbook,
            individual_assessments,
            item.final_attempt,
            student.sis_user_id,
            student.canvas_id,
            snr,
            student.name,
        )

    item.individual_assessments = individual_assessments
    item.individual_assessment_found = bool(individual_assessments)

    grading_record = lookup_grading_record(
        overview_workbook,
        item.final_attempt,
        student.sis_user_id,
        student.canvas_id,
        snr,
        student.name,
    )
    supervisor_name = clean_text(
        grading_record.get("supervisor_name", "") if grading_record else ""
    )
    second_reader_name = clean_text(
        grading_record.get("second_reader_name", "") if grading_record else ""
    )
    item.supervisor_name_found = bool(supervisor_name)
    item.second_reader_name_found = bool(second_reader_name)

    item.first_reader_overall_grade = ""
    item.second_reader_overall_grade = ""
    item.individual_overall_grade = ""
    item.individual_criterion_scores = 0
    if grading_record:
        item.first_reader_overall_grade, item.second_reader_overall_grade = (
            reader_overall_grades(grading_record)
        )
        item.individual_overall_grade, _, _, item.individual_criterion_scores = (
            summarize_individual_grades(grading_record, item.final_attempt)
        )

    final_submission = (
        second
        if item.final_attempt == "Second"
        else first
        if item.final_attempt == "First"
        else None
    )
    joint_assessment = item.joint_assessment
    joint_grade = (
        joint_assessment.summary.consolidated_grade
        if joint_assessment and joint_assessment.summary.consolidated_grade
        else ""
    )

    archive_metadata = lookup_archive_metadata(
        overview_workbook,
        item.final_attempt,
        student.sis_user_id,
        student.canvas_id,
        fallback_name=student.name,
        fallback_section=student.section,
        overview_snr=snr,
    )
    item.archive_metadata = enrich_archive_metadata(
        archive_metadata,
        final_submission=final_submission,
        student_name=student.name,
        sis_user_id=snr or student.sis_user_id,
        canvas_id=student.canvas_id,
        section=student.section,
        individual_overall_grade=item.individual_overall_grade,
        final_score=student.final_score,
        override_score=student.override_score,
        joint_grade=joint_grade,
    )
    if item.archive_metadata and item.archive_metadata.sis_user_id:
        item.overview_snr = item.archive_metadata.sis_user_id
    overview_name = lookup_overview_student_name(
        overview_workbook,
        sis_user_id=snr or student.sis_user_id,
        canvas_id=student.canvas_id,
        overview_snr=snr,
        canvas_name=student.name,
    )
    if overview_name:
        item.overview_student_name = overview_name
        if item.archive_metadata:
            item.archive_metadata.student_name = overview_name

    final_assessment = None
    if individual_assessments and item.final_attempt:
        for assessment in individual_assessments:
            if assessment.attempt == item.final_attempt:
                final_assessment = assessment
                break
    if final_assessment is None and individual_assessments:
        final_assessment = individual_assessments[-1]

    item.first_reader_assessment_found = False
    item.second_reader_assessment_found = False
    if final_assessment is not None:
        item.first_reader_assessment_found = final_assessment.first_reader is not None
        item.second_reader_assessment_found = final_assessment.second_reader is not None


def process_students(
    enrollments: list[dict],
    first_submissions: dict[str, SubmissionRecord],
    second_submissions: dict[str, SubmissionRecord],
    first_pdfs: list[Path],
    second_pdfs: list[Path],
    pass_threshold: float,
    assessment_workbook: dict[str, list[AssessmentRow]] | None,
    assessment_workbook_provided: bool,
    overview_workbook: OverviewWorkbook | None,
    overview_workbook_provided: bool,
    use_canvas_attachments: bool = False,
) -> list[ProcessedStudent]:
    processed: list[ProcessedStudent] = []
    progress = ConsoleProgress(len(enrollments), "Processing students") if enrollments else None

    for enrollment in enrollments:
        student = build_student_record(enrollment, pass_threshold)
        first = first_submissions.get(student.canvas_id)
        second = second_submissions.get(student.canvas_id)

        from thesis2lib.scoring import (
            apply_last_submission_grade,
            choose_last_submission_score,
            fill_missing_gradebook_scores,
        )

        result_threshold, pass_above_threshold = apply_last_submission_grade(
            student,
            first.raw if first else None,
            second.raw if second else None,
            pass_threshold,
        )

        if student.final_score is None or student.override_score is None:
            submission_score, _ = choose_last_submission_score(
                first.raw if first else None,
                second.raw if second else None,
            )
            if submission_score is not None:
                fill_missing_gradebook_scores(student, submission_score)

        student.course_result = determine_course_result(
            student.official_score,
            first.raw if first else None,
            second.raw if second else None,
            result_threshold,
            pass_above_threshold=pass_above_threshold,
        )
        final_attempt = choose_final_attempt(
            first.raw if first else None,
            second.raw if second else None,
        )

        pdf_selection: PdfSelection | None = None
        if student.course_result == "Passed" and final_attempt:
            if use_canvas_attachments:
                pdf_selection = select_library_canvas_attachments(
                    first.raw if first else None,
                    second.raw if second else None,
                    final_attempt,
                )
            else:
                pdf_selection = select_library_pdf_candidate(
                    first_pdfs,
                    second_pdfs,
                    first.raw if first else None,
                    second.raw if second else None,
                    final_attempt,
                    student.sis_user_id,
                    student.canvas_id,
                )

        overview_snr = ""
        if overview_workbook_provided and overview_workbook:
            overview_snr = resolve_overview_snr(
                overview_workbook,
                student.sis_user_id,
                student.canvas_id,
                student.name,
            )

        individual_assessments: list[IndividualAssessmentData] = []
        individual_found = False
        supervisor_found = False
        second_reader_found = False
        individual_overall_grade = ""
        first_reader_overall_grade = ""
        second_reader_overall_grade = ""
        individual_criterion_scores = 0
        grading_record = None

        assessment_attempts: list[str] = []
        if has_real_submission(first.raw if first else None):
            assessment_attempts.append("First")
        if has_real_submission(second.raw if second else None):
            assessment_attempts.append("Second")

        if overview_workbook_provided and overview_workbook and assessment_attempts:
            individual_assessments = extract_all_individual_assessments(
                overview_workbook,
                assessment_attempts,
                student.sis_user_id,
                student.canvas_id,
                overview_snr,
                student.name,
            )
            individual_found = bool(individual_assessments)
            if final_attempt:
                grading_record = lookup_grading_record(
                    overview_workbook,
                    final_attempt,
                    student.sis_user_id,
                    student.canvas_id,
                    overview_snr,
                    student.name,
                )
            elif individual_assessments:
                grading_record = lookup_grading_record(
                    overview_workbook,
                    individual_assessments[-1].attempt,
                    student.sis_user_id,
                    student.canvas_id,
                    overview_snr,
                    student.name,
                )
            supervisor_name = clean_text(
                grading_record.get("supervisor_name", "") if grading_record else ""
            )
            second_reader_name = clean_text(
                grading_record.get("second_reader_name", "") if grading_record else ""
            )
            supervisor_found = bool(supervisor_name)
            second_reader_found = bool(second_reader_name)
            if grading_record:
                first_reader_overall_grade, second_reader_overall_grade = (
                    reader_overall_grades(grading_record)
                )
            if grading_record and final_attempt:
                individual_overall_grade, _, _, individual_criterion_scores = (
                    summarize_individual_grades(grading_record, final_attempt)
                )
            elif grading_record:
                individual_overall_grade, _, _, individual_criterion_scores = (
                    summarize_individual_grades(
                        grading_record, individual_assessments[-1].attempt
                    )
                )
        elif assessment_workbook_provided:
            individual_found, supervisor_found, second_reader_found = (
                lookup_individual_assessment(
                    assessment_workbook,
                    final_attempt,
                    student.sis_user_id,
                    student.canvas_id,
                )
            )

        if overview_workbook_provided and overview_workbook:
            pdf_snr = overview_snr_from_pdf_selection(pdf_selection)
            if pdf_snr:
                overview_snr = pdf_snr
            if assessment_attempts and not individual_assessments:
                individual_assessments = extract_all_individual_assessments(
                    overview_workbook,
                    assessment_attempts,
                    student.sis_user_id,
                    student.canvas_id,
                    overview_snr,
                    student.name,
                )
            individual_assessments = ensure_final_attempt_assessments(
                overview_workbook,
                individual_assessments,
                final_attempt,
                student.sis_user_id,
                student.canvas_id,
                overview_snr,
                student.name,
            )
            if individual_assessments:
                individual_found = True
                if final_attempt:
                    grading_record = lookup_grading_record(
                        overview_workbook,
                        final_attempt,
                        student.sis_user_id,
                        student.canvas_id,
                        overview_snr,
                        student.name,
                    )
                    if grading_record:
                        first_reader_overall_grade, second_reader_overall_grade = (
                            reader_overall_grades(grading_record)
                        )
                        individual_overall_grade, _, _, individual_criterion_scores = (
                            summarize_individual_grades(grading_record, final_attempt)
                        )

        final_submission = (
            second
            if final_attempt == "Second"
            else first
            if final_attempt == "First"
            else None
        )
        joint_assessment = extract_joint_assessment(final_submission)
        submission_comments = extract_submission_comments(final_submission)
        joint_found = joint_assessment is not None
        joint_rubric_criteria = len(joint_assessment.criteria) if joint_assessment else 0
        joint_rubric_points = (
            joint_assessment.summary.rubric_points if joint_assessment else None
        )

        archive_metadata = lookup_archive_metadata(
            overview_workbook,
            final_attempt,
            student.sis_user_id,
            student.canvas_id,
            fallback_name=student.name,
            fallback_section=student.section,
            overview_snr=overview_snr,
        )
        joint_grade = (
            joint_assessment.summary.consolidated_grade
            if joint_assessment and joint_assessment.summary.consolidated_grade
            else ""
        )
        archive_metadata = enrich_archive_metadata(
            archive_metadata,
            final_submission=final_submission,
            student_name=student.name,
            sis_user_id=overview_snr or student.sis_user_id,
            canvas_id=student.canvas_id,
            section=student.section,
            individual_overall_grade=individual_overall_grade,
            final_score=student.final_score,
            override_score=student.override_score,
            joint_grade=joint_grade,
        )
        if archive_metadata and archive_metadata.sis_user_id:
            overview_snr = archive_metadata.sis_user_id

        overview_student_name = ""
        if overview_workbook_provided and overview_workbook:
            overview_student_name = lookup_overview_student_name(
                overview_workbook,
                sis_user_id=overview_snr or student.sis_user_id,
                canvas_id=student.canvas_id,
                overview_snr=overview_snr,
                canvas_name=student.name,
            )
            if overview_student_name and archive_metadata:
                archive_metadata.student_name = overview_student_name

        first_reader_assessment_found, second_reader_assessment_found = (
            False,
            False,
        )
        final_assessment = None
        if individual_assessments and final_attempt:
            for assessment in individual_assessments:
                if assessment.attempt == final_attempt:
                    final_assessment = assessment
                    break
        if final_assessment is None and individual_assessments:
            final_assessment = individual_assessments[-1]
        if final_assessment is not None:
            first_reader_assessment_found = final_assessment.first_reader is not None
            second_reader_assessment_found = final_assessment.second_reader is not None

        package_status, details = determine_package_status(
            student.course_result,
            final_attempt,
            pdf_selection,
            individual_found,
            joint_found,
            assessment_workbook_provided,
            overview_workbook_provided,
            archive_metadata,
        )

        processed.append(
            ProcessedStudent(
                student=student,
                first_submission=first,
                second_submission=second,
                final_attempt=final_attempt,
                pdf_selection=pdf_selection,
                individual_assessment_found=individual_found,
                first_reader_assessment_found=first_reader_assessment_found,
                second_reader_assessment_found=second_reader_assessment_found,
                supervisor_name_found=supervisor_found,
                second_reader_name_found=second_reader_found,
                joint_assessment_found=joint_found,
                joint_assessment=joint_assessment,
                submission_comments=submission_comments,
                individual_assessments=individual_assessments,
                archive_metadata=archive_metadata,
                overview_snr=overview_snr,
                overview_student_name=overview_student_name,
                individual_overall_grade=individual_overall_grade,
                first_reader_overall_grade=first_reader_overall_grade,
                second_reader_overall_grade=second_reader_overall_grade,
                individual_criterion_scores=individual_criterion_scores,
                joint_rubric_criteria=joint_rubric_criteria,
                joint_rubric_points=joint_rubric_points,
                package_status=package_status,
                details=details,
            )
        )
        if progress:
            progress.advance(student.name[:36])

    if progress:
        progress.finish()
    return processed


def refresh_all_package_statuses(
    students: list[ProcessedStudent],
    *,
    assessment_workbook_provided: bool,
    overview_workbook_provided: bool,
) -> None:
    for item in students:
        item.package_status, item.details = determine_package_status(
            item.student.course_result,
            item.final_attempt,
            item.pdf_selection,
            item.individual_assessment_found,
            item.joint_assessment_found,
            assessment_workbook_provided,
            overview_workbook_provided,
            item.archive_metadata,
        )
