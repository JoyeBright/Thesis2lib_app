"""Application entry point."""

from __future__ import annotations

import logging
import sys

from thesis2lib.overview_reader import load_overview_workbook
from thesis2lib.canvas_records import (
    deduplicate_enrollments,
    filter_student_enrollments,
    submission_map,
)
from thesis2lib.cli import (
    parse_args,
    print_summary,
    prompt_for_canvas_token,
    require_token_flag,
    resolve_interactive_review,
    resolve_pdf_input_mode,
    validate_runtime,
)
from thesis2lib.logging_config import configure_logging
from thesis2lib.archive_storage import create_archive_storage
from thesis2lib.delivery import (
    archive_storage_root,
    ensure_embargo_folder,
    ensure_not_open_access_folder,
    gradebook_filename,
    graduates_report_filename,
)
from thesis2lib.gradebook_writer import write_gradebook
from thesis2lib.graduates_report_writer import write_graduates_report
from thesis2lib.library_delivery import DEFAULT_TITLE_WORKERS, populate_thesis_titles
from thesis2lib.download_pool import DEFAULT_DOWNLOAD_WORKERS
from thesis2lib.open_access_writer import create_open_access_pdfs
from thesis2lib.package_writer import create_sop_packages
from thesis2lib.pipeline import process_students, refresh_all_package_statuses
from thesis2lib.progress import count_pipeline_steps, pipeline_step
from thesis2lib.report_writer import write_processing_report
from thesis2lib.utils import list_pdfs


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    require_token_flag(args)

    try:
        token = prompt_for_canvas_token()
        pdf_mode = resolve_pdf_input_mode(args)

        if args.validate_only:
            validate_runtime(args, token)
            print("Validation succeeded.")
            return 0

        client, raw_enrollments = validate_runtime(args, token)

        output_folder = args.output_folder.expanduser().resolve()
        report_path = output_folder / args.processing_report
        download_workers = max(1, args.download_workers or DEFAULT_DOWNLOAD_WORKERS)
        title_workers = max(1, args.title_workers or DEFAULT_TITLE_WORKERS)

        overview_workbook_provided = args.overview_file is not None
        assessment_workbook_provided = args.assessment_file is not None
        step_total = count_pipeline_steps(
            report_only=args.report_only,
            has_overview=overview_workbook_provided,
            has_assessment=assessment_workbook_provided,
        )
        step = 0

        first_pdfs: list = []
        second_pdfs: list = []
        if pdf_mode == "local":
            first_pdf_folder = args.first_pdf_folder.expanduser().resolve()
            second_pdf_folder = args.second_pdf_folder.expanduser().resolve()
            first_pdfs = list_pdfs(first_pdf_folder)
            second_pdfs = list_pdfs(second_pdf_folder)

        student_enrollments = filter_student_enrollments(raw_enrollments)
        enrollments = deduplicate_enrollments(student_enrollments)

        overview_workbook = None
        if overview_workbook_provided:
            step += 1
            with pipeline_step(step, step_total, "Loading overview workbook") as progress:
                overview_workbook = load_overview_workbook(
                    args.overview_file.expanduser().resolve(),
                    args.semester,
                )
                progress.finish("workbook loaded")

        step += 1
        with pipeline_step(step, step_total, "Fetching Canvas submissions") as progress:
            first_raw = client.get_submissions(args.course_id, args.first_assignment_id)
            second_raw = client.get_submissions(args.course_id, args.second_assignment_id)
            first_submissions = submission_map(first_raw, args.first_assignment_id)
            second_submissions = submission_map(second_raw, args.second_assignment_id)
            progress.finish(
                f"{len(first_submissions)} first, {len(second_submissions)} second"
            )

        from thesis2lib.grading_scale import configure_grading_scale

        configure_grading_scale(
            submission_score_max=args.submission_score_max,
            gradebook_score_max=args.gradebook_score_max,
            course_grade_max=args.course_grade_max,
            course_pass_grade=args.course_pass_grade,
            pass_threshold=args.pass_threshold,
            client=client,
            course_id=args.course_id,
            first_assignment_id=args.first_assignment_id,
            second_assignment_id=args.second_assignment_id,
            enrollments=enrollments,
            submission_rows=[*first_raw, *second_raw],
        )

        assessment_workbook = None
        if assessment_workbook_provided:
            step += 1
            with pipeline_step(step, step_total, "Loading assessment workbook") as progress:
                from thesis2lib.assessment_reader import load_assessment_workbook

                assessment_workbook = load_assessment_workbook(
                    args.assessment_file.expanduser().resolve()
                )
                progress.finish("workbook loaded")

        if not enrollments:
            raise RuntimeError(
                "No Canvas student enrollments remain after filtering. "
                "Check that the course has student enrollments in Canvas."
            )

        step += 1
        with pipeline_step(step, step_total, "Processing students") as progress:
            students = process_students(
                enrollments,
                first_submissions,
                second_submissions,
                first_pdfs,
                second_pdfs,
                assessment_workbook,
                assessment_workbook_provided,
                overview_workbook,
                overview_workbook_provided,
                use_canvas_attachments=(pdf_mode == "canvas"),
            )
            progress.finish(f"{len(students)} student(s)")

        step += 1
        with pipeline_step(step, step_total, "Extracting thesis titles") as progress:
            title_count = populate_thesis_titles(
                students,
                output_folder,
                client=client if pdf_mode == "canvas" else None,
                use_canvas=pdf_mode == "canvas",
                first_pdf_pool=first_pdfs if pdf_mode == "local" else None,
                second_pdf_pool=second_pdfs if pdf_mode == "local" else None,
                overwrite=args.overwrite_output,
                skip_existing_downloads=args.skip_existing_downloads,
                workers=title_workers,
                download_workers=download_workers,
            )
            progress.finish(f"{title_count} title(s)")

        from thesis2lib.manual_review import CanvasReviewContext, review_manual_decisions

        canvas_context = CanvasReviewContext(
            canvas_url=args.canvas_url,
            course_id=args.course_id,
            first_assignment_id=args.first_assignment_id,
            second_assignment_id=args.second_assignment_id,
            open_browser=resolve_interactive_review(args) and not args.no_open_canvas,
        )

        step += 1
        with pipeline_step(step, step_total, "Checking flagged students") as progress:
            review_count, flagged_count = review_manual_decisions(
                students,
                output_folder,
                interactive=resolve_interactive_review(args),
                decisions_file=args.manual_decisions_file,
                client=client if pdf_mode == "canvas" else None,
                assessment_workbook_provided=assessment_workbook_provided,
                overview_workbook_provided=overview_workbook_provided,
                overview_workbook=overview_workbook,
                canvas_context=canvas_context,
            )
            if flagged_count:
                progress.finish(f"{flagged_count} reviewed")
            else:
                progress.finish("none needed")

        step += 1
        with pipeline_step(step, step_total, "Writing processing report") as progress:
            refresh_all_package_statuses(
                students,
                assessment_workbook_provided=assessment_workbook_provided,
                overview_workbook_provided=overview_workbook_provided,
            )
            write_processing_report(
                students,
                report_path,
            )
            progress.finish(str(report_path.name))

        archive_root = archive_storage_root(output_folder)
        archive_root.mkdir(parents=True, exist_ok=True)

        step += 1
        with pipeline_step(step, step_total, "Writing Canvas gradebook") as progress:
            derived_scores = None
            write_gradebook(
                client,
                args.course_id,
                raw_enrollments,
                archive_root / gradebook_filename(args.semester, args.programme),
                args.overwrite_output,
                derived_scores=derived_scores,
            )
            progress.finish("saved to Archive/")

        step += 1
        with pipeline_step(step, step_total, "Writing graduates report") as progress:
            graduates_path = write_graduates_report(
                students,
                output_folder,
                output_folder / graduates_report_filename(args.semester, args.programme),
                args.semester,
                args.programme,
                args.overwrite_output,
            )
            progress.finish(
                graduates_path.name if graduates_path else "no passing graduates"
            )

        output_folder.mkdir(parents=True, exist_ok=True)

        packages_created = 0
        open_access_count = 0
        archive_files = 0
        if not args.report_only:
            step += 1
            with pipeline_step(step, step_total, "Preparing delivery folders") as progress:
                ensure_embargo_folder(output_folder)
                ensure_not_open_access_folder(output_folder)
                progress.finish("Embargo and Not open access")

            step += 1
            with pipeline_step(step, step_total, "Building archive storage") as progress:
                archive_files = create_archive_storage(
                    students,
                    output_folder,
                    args.overwrite_output,
                    client if pdf_mode == "canvas" else None,
                    first_pdfs if pdf_mode == "local" else None,
                    second_pdfs if pdf_mode == "local" else None,
                    skip_existing_downloads=args.skip_existing_downloads,
                    download_workers=download_workers,
                )
                progress.finish(f"{archive_files} PDF(s)")

            step += 1
            with pipeline_step(step, step_total, "Building Open Access folder") as progress:
                open_access_count = create_open_access_pdfs(
                    students,
                    output_folder,
                    args.semester,
                    args.programme,
                    args.overwrite_output,
                )
                progress.finish(f"{open_access_count} PDF(s)")

            step += 1
            with pipeline_step(step, step_total, "Building SOP packages") as progress:
                packages_created = create_sop_packages(
                    students,
                    output_folder,
                    args.overwrite_output,
                    client if pdf_mode == "canvas" else None,
                    skip_existing_downloads=args.skip_existing_downloads,
                )
                progress.finish(f"{packages_created} package(s)")

        print_summary(
            students,
            report_path,
            packages_created,
            args.report_only,
            args.verbose_personal_data,
            args.programme,
            args.semester,
        )
        return 0

    except SystemExit:
        raise
    except Exception as exc:
        logging.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
