"""Command-line interface and runtime validation."""

from __future__ import annotations

import argparse
import logging
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import load_workbook

from thesis2lib.config_loader import load_config_defaults, validate_required_settings
from thesis2lib.overview_reader import validate_overview_workbook
from thesis2lib.canvas_client import CanvasClient
from thesis2lib.constants import (
    ASSESSMENT_SHEET_ALIASES,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PASS_THRESHOLD,
    DEFAULT_PROCESSING_REPORT,
)
from thesis2lib.library_delivery import DEFAULT_TITLE_WORKERS
from thesis2lib.download_pool import DEFAULT_DOWNLOAD_WORKERS
from thesis2lib.models import ProcessedStudent
from thesis2lib.report_writer import build_summary_counts


DEFAULT_CONFIG_FILE = "config.yml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help=f"YAML config file with static settings. Default: {DEFAULT_CONFIG_FILE}",
    )
    pre_args, remaining = pre_parser.parse_known_args(argv)

    config_path = Path(pre_args.config).expanduser()
    config_defaults = load_config_defaults(config_path)

    parser = argparse.ArgumentParser(
        description=(
            "Cross-reference local thesis PDF folders with Canvas enrollment "
            "and submission data."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(config_path),
        help=f"YAML config file with static settings. Default: {DEFAULT_CONFIG_FILE}",
    )
    parser.add_argument(
        "--canvas-url",
        default=config_defaults.get("canvas_url"),
        help="Canvas base URL.",
    )
    parser.add_argument(
        "--course-id",
        type=int,
        default=config_defaults.get("course_id"),
        help="Canvas course ID.",
    )
    parser.add_argument(
        "--first-assignment-id",
        type=int,
        default=config_defaults.get("first_assignment_id"),
        help="Canvas assignment ID for the first submission.",
    )
    parser.add_argument(
        "--second-assignment-id",
        type=int,
        default=config_defaults.get("second_assignment_id"),
        help="Canvas assignment ID for the second submission.",
    )
    parser.add_argument(
        "--first-pdf-folder",
        type=Path,
        default=config_defaults.get("first_pdf_folder"),
        help=(
            "Optional local folder containing first-submission PDFs. "
            "Omit both PDF folders to use Canvas submission attachments instead."
        ),
    )
    parser.add_argument(
        "--second-pdf-folder",
        type=Path,
        default=config_defaults.get("second_pdf_folder"),
        help=(
            "Optional local folder containing second-submission PDFs. "
            "Omit both PDF folders to use Canvas submission attachments instead."
        ),
    )
    parser.add_argument(
        "--output-folder",
        type=Path,
        default=config_defaults.get("output_folder"),
        help="Destination folder for student packages and the processing report.",
    )
    parser.add_argument(
        "--programme",
        default=config_defaults.get("programme"),
        help="Programme code, e.g. MSc_DSS.",
    )
    parser.add_argument(
        "--semester",
        default=config_defaults.get("semester"),
        help="Semester code, e.g. F2025.",
    )
    parser.add_argument(
        "--overview-file",
        type=Path,
        default=config_defaults.get("overview_file"),
        help=(
            "Overview workbook (.xlsx) with Matching_<semester>, "
            "Grading_1st, and Grading_2nd sheets."
        ),
    )
    parser.add_argument(
        "--assessment-file",
        type=Path,
        default=config_defaults.get("assessment_file"),
        help="Optional local assessment workbook (.xlsx).",
    )
    parser.add_argument(
        "--course-pass-grade",
        type=float,
        default=config_defaults.get("course_pass_grade"),
        help="Passing course final grade (default: 5.5 on the 0–10 scale).",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=config_defaults.get("pass_threshold", DEFAULT_PASS_THRESHOLD),
        help=(
            "Legacy: gradebook pass %% (e.g. 60). Prefer course_pass_grade: 5.5 in config."
        ),
    )
    parser.add_argument(
        "--submission-score-max",
        type=float,
        default=config_defaults.get("submission_score_max"),
        help=(
            "Max points on thesis submission assignments. "
            "Default: read from Canvas assignment points_possible."
        ),
    )
    parser.add_argument(
        "--gradebook-score-max",
        type=float,
        default=config_defaults.get("gradebook_score_max"),
        help=(
            "Max for Canvas Final/Override Score in the gradebook (often 100; "
            "set if your course uses another scale)."
        ),
    )
    parser.add_argument(
        "--course-grade-max",
        type=float,
        default=config_defaults.get("course_grade_max"),
        help="Max for library course final grade display (default: 10).",
    )
    parser.add_argument(
        "--prompt-for-token",
        action="store_true",
        help="Prompt interactively for the Canvas API token.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate inputs and Canvas access without copying files.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Generate the processing report without copying PDFs.",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Replace existing Open Access, SOP, and report outputs when rerunning.",
    )
    parser.add_argument(
        "--skip-existing-downloads",
        action="store_true",
        help=(
            "Keep existing thesis cache and Archive PDFs; do not re-download from Canvas."
        ),
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=config_defaults.get("download_workers") or DEFAULT_DOWNLOAD_WORKERS,
        metavar="N",
        help=(
            "Parallel workers for Canvas PDF downloads "
            f"(default: {DEFAULT_DOWNLOAD_WORKERS}). Use 1 to disable."
        ),
    )
    parser.add_argument(
        "--title-workers",
        type=int,
        default=config_defaults.get("title_workers"),
        metavar="N",
        help=(
            "Parallel workers for thesis title extraction "
            f"(default: auto, up to {DEFAULT_TITLE_WORKERS})."
        ),
    )
    parser.add_argument(
        "--no-open-canvas",
        action="store_true",
        help="During manual checks, print Canvas URLs but do not open the browser.",
    )
    parser.add_argument(
        "--no-interactive-review",
        action="store_true",
        help="Skip terminal prompts for low-confidence PDFs and titles.",
    )
    parser.add_argument(
        "--manual-decisions-file",
        type=Path,
        default=config_defaults.get("manual_decisions_file"),
        help=(
            "YAML file for saved manual PDF/title decisions. "
            "Default: <output>/Manual/manual_decisions.yml"
        ),
    )
    parser.add_argument(
        "--processing-report",
        default=DEFAULT_PROCESSING_REPORT,
        help=f"Processing report filename. Default: {DEFAULT_PROCESSING_REPORT}.",
    )
    parser.add_argument(
        "--log-level",
        default=DEFAULT_LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=f"Logging level. Default: {DEFAULT_LOG_LEVEL}.",
    )
    parser.add_argument(
        "--verbose-personal-data",
        action="store_true",
        help="Print student-level details to the console.",
    )
    args = parser.parse_args(remaining)
    validate_required_settings(args)
    return args


def resolve_interactive_review(args: argparse.Namespace) -> bool:
    return not args.no_interactive_review and sys.stdin.isatty()


def validate_canvas_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Canvas URL must use HTTPS.")
    if not parsed.netloc:
        raise ValueError("Canvas URL is invalid.")


def resolve_pdf_input_mode(args: argparse.Namespace) -> str:
    first = args.first_pdf_folder
    second = args.second_pdf_folder
    if first is None and second is None:
        return "canvas"
    if first is not None and second is not None:
        return "local"
    raise ValueError(
        "Provide both --first-pdf-folder and --second-pdf-folder, "
        "or omit both to download thesis PDFs from Canvas."
    )


def prompt_for_canvas_token() -> str:
    token = getpass("Canvas API token: ").strip()
    if not token:
        raise RuntimeError("No Canvas API token was provided.")
    return token


def validate_runtime(
    args: argparse.Namespace, token: str
) -> tuple[CanvasClient, list[dict]]:
    validate_canvas_url(args.canvas_url)
    pdf_mode = resolve_pdf_input_mode(args)

    output_folder = args.output_folder.expanduser().resolve()

    if pdf_mode == "local":
        first_pdf_folder = args.first_pdf_folder.expanduser().resolve()
        second_pdf_folder = args.second_pdf_folder.expanduser().resolve()
        if not first_pdf_folder.is_dir():
            raise FileNotFoundError(f"First PDF folder not found: {first_pdf_folder}")
        if not second_pdf_folder.is_dir():
            raise FileNotFoundError(f"Second PDF folder not found: {second_pdf_folder}")

    output_folder.mkdir(parents=True, exist_ok=True)
    probe = output_folder / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise PermissionError(f"Output folder is not writable: {output_folder}") from exc

    if args.assessment_file:
        assessment_path = args.assessment_file.expanduser().resolve()
        if not assessment_path.is_file():
            raise FileNotFoundError(f"Assessment workbook not found: {assessment_path}")
        workbook = load_workbook(assessment_path, read_only=True)
        for sheet in ASSESSMENT_SHEET_ALIASES.values():
            if sheet not in workbook.sheetnames:
                raise ValueError(
                    f"Assessment workbook is missing required sheet: {sheet}"
                )
        workbook.close()

    if args.overview_file:
        overview_path = args.overview_file.expanduser().resolve()
        if not overview_path.is_file():
            raise FileNotFoundError(f"Overview workbook not found: {overview_path}")
        validate_overview_workbook(overview_path, args.semester)

    if not token:
        raise RuntimeError("Canvas API token is required.")

    client = CanvasClient(args.canvas_url, token)
    client.validate_course(args.course_id)
    client.validate_assignment(args.course_id, args.first_assignment_id)
    client.validate_assignment(args.course_id, args.second_assignment_id)

    enrollments = client.get_enrollments(args.course_id)
    if not enrollments:
        raise RuntimeError("No student enrollments were retrieved from Canvas.")

    if enrollments[0].get("grades") is None:
        raise RuntimeError(
            "Enrollment grade fields are not accessible. Check Canvas permissions."
        )

    from thesis2lib.thesis_title_ocr import ensure_tesseract_available

    version = ensure_tesseract_available()
    logging.info("Tesseract OCR available (version %s).", version)

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
        submission_rows=None,
    )

    return client, enrollments


def print_summary(
    students: list[ProcessedStudent],
    report_path: Path,
    packages_created: int,
    report_only: bool,
    verbose: bool,
    programme: str,
    semester: str,
) -> None:
    summary = build_summary_counts(students)
    print("Thesis2Lib processing completed.")
    print(f"Programme: {programme}")
    print(f"Semester: {semester}")
    print(f"Canvas students processed: {len(students)}")
    print(f"Report: {report_path}")
    for _, row in summary.iterrows():
        print(f"{row['Status']}: {row['Student Count']}")

    if report_only:
        print("Report-only mode: no PDFs were copied.")
    else:
        print(f"Packages created or preserved: {packages_created}")

    if verbose:
        for item in students:
            print(
                f"{item.student.name} | {item.student.sis_user_id} | "
                f"{item.package_status} | {item.details}"
            )


def require_token_flag(args: argparse.Namespace) -> None:
    if not args.prompt_for_token:
        print(
            "ERROR: Canvas access requires --prompt-for-token.",
            file=sys.stderr,
        )
        raise SystemExit(2)
