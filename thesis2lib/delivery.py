"""SOP file and folder naming for library delivery packages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from thesis2lib.constants import INVALID_FILENAME_CHARS
from thesis2lib.models import ProcessedStudent
from thesis2lib.text_polish import normalize_unicode, polish_thesis_title
from thesis2lib.utils import clean_text, normalize_identifier, sanitize_folder_name


@dataclass(frozen=True)
class DeliveryPaths:
    student_number: str
    name_slug: str
    folder: Path
    thesis_pdf: Path
    assessments: Path


def open_access_folder_name(semester: str, programme: str) -> str:
    semester_part = sanitize_folder_name(semester) or "Semester"
    programme_part = sanitize_folder_name(programme) or "Programme"
    return (
        f"Open Access – {semester_part}-{programme_part} "
        "Consolidated thesis for library"
    )


def open_access_root(output_folder: Path, semester: str, programme: str) -> Path:
    return output_folder / open_access_folder_name(semester, programme)


def sanitize_author_filename_part(value: str, *, max_length: int = 80) -> str:
    """Sanitize a person name for library filenames without thesis OCR cleanup."""
    cleaned = normalize_unicode(clean_text(value))
    cleaned = INVALID_FILENAME_CHARS.sub("_", cleaned)
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned


def sanitize_filename_part(value: str, *, max_length: int = 120) -> str:
    cleaned = polish_thesis_title(normalize_unicode(clean_text(value)))
    cleaned = INVALID_FILENAME_CHARS.sub("_", cleaned)
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "Unknown"


def library_author_name(student_name: str) -> str:
    text = clean_text(student_name)
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        if last or first:
            formatted = sanitize_author_filename_part(f"{last}, {first}")
            if formatted:
                return formatted
    parts = text.split()
    if len(parts) >= 2:
        last = parts[-1]
        first = " ".join(parts[:-1])
        formatted = sanitize_author_filename_part(f"{last}, {first}")
        if formatted:
            return formatted
    formatted = sanitize_author_filename_part(text)
    if formatted:
        return formatted
    return sanitize_author_filename_part(text or "Unknown Student") or "Unknown Student"


def resolve_library_student_name(item: ProcessedStudent) -> str:
    """Prefer the overview workbook name for library delivery filenames."""
    placeholders = {"", "unknown", "unknown student"}
    for candidate in (
        item.overview_student_name,
        item.archive_metadata.student_name if item.archive_metadata else "",
        item.student.name,
    ):
        name = clean_text(candidate)
        if name.casefold() not in placeholders:
            return name
    snr = resolve_archive_student_number(item)
    if snr:
        return f"Student {snr}"
    canvas_id = normalize_identifier(item.student.canvas_id)
    if canvas_id:
        return f"Student {canvas_id}"
    return "Unknown Student"


def open_access_pdf_filename(student_name: str, thesis_title: str) -> str:
    author = library_author_name(student_name)
    title = sanitize_filename_part(thesis_title, max_length=160) or "Thesis"
    return f"{author}_{title}.pdf"


def open_access_pdf_filename_for_item(item: ProcessedStudent) -> str:
    return open_access_pdf_filename(
        resolve_library_student_name(item),
        item.thesis_title or "",
    )


def open_access_pdf_path(
    open_access_root: Path,
    student_name: str,
    thesis_title: str,
) -> Path:
    return open_access_root / open_access_pdf_filename(student_name, thesis_title)


def open_access_pdf_path_for_item(
    open_access_root: Path,
    item: ProcessedStudent,
) -> Path:
    return open_access_root / open_access_pdf_filename_for_item(item)


def unique_delivery_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


EMBARGO_FOLDER = "2-year Embargo"
NOT_OPEN_ACCESS_FOLDER = "Not open access"
FULL_ARCHIVE_FOLDER = "Archive"
SOP_FOLDER = "SOP"


def submission_folder_name(semester: str, programme: str) -> str:
    semester_part = sanitize_folder_name(semester) or "Semester"
    programme_part = sanitize_folder_name(programme) or "Programme"
    return f"{semester_part}-{programme_part}"


def gradebook_filename(semester: str, programme: str) -> str:
    return f"{submission_folder_name(semester, programme)}_Canvas_Gradebook.xlsx"


def graduates_report_filename(semester: str, programme: str) -> str:
    return f"{submission_folder_name(semester, programme)}_Graduates_Report.xlsx"


def embargo_root(output_folder: Path) -> Path:
    return output_folder / EMBARGO_FOLDER


def not_open_access_root(output_folder: Path) -> Path:
    return output_folder / NOT_OPEN_ACCESS_FOLDER


def archive_storage_root(output_folder: Path) -> Path:
    return output_folder / FULL_ARCHIVE_FOLDER


def sop_root(output_folder: Path) -> Path:
    return output_folder / SOP_FOLDER


def delivery_name_slug(student_name: str) -> str:
    text = clean_text(student_name)
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        text = f"{last}{first}" if last or first else text
    compact = re.sub(r"\s+", "", text)
    return sanitize_folder_name(compact) or "Student"


def resolve_archive_student_number(item: ProcessedStudent) -> str:
    overview_snr = normalize_identifier(item.overview_snr)
    if overview_snr:
        return overview_snr
    if item.archive_metadata and item.archive_metadata.sis_user_id:
        return normalize_identifier(item.archive_metadata.sis_user_id)
    canvas_sis = normalize_identifier(item.student.sis_user_id)
    if canvas_sis:
        return canvas_sis
    return normalize_identifier(item.student.canvas_id)


def student_final_grade(item: ProcessedStudent) -> str:
    from thesis2lib.scoring import format_course_final_grade_ten_scale

    return format_course_final_grade_ten_scale(item.student)


def attempt_folder_name(attempt: str) -> str:
    return "First Submission" if attempt == "First" else "Second Submission"


def delivery_paths(
    output_root: Path,
    student_number: str,
    student_name: str,
) -> DeliveryPaths:
    number = sanitize_folder_name(student_number) or "UnknownID"
    name_slug = delivery_name_slug(student_name)
    folder = output_root / f"ARCHIVE_{name_slug}_{number}"
    file_prefix = f"{name_slug}_{number}"
    return DeliveryPaths(
        student_number=number,
        name_slug=name_slug,
        folder=folder,
        thesis_pdf=folder / f"{file_prefix}_Thesis.pdf",
        assessments=folder / f"{number}_Assessments.xlsx",
    )


def delivery_paths_for_student(
    output_root: Path,
    item: ProcessedStudent,
) -> DeliveryPaths:
    return delivery_paths(
        output_root,
        resolve_archive_student_number(item),
        item.student.name,
    )


def ensure_embargo_folder(output_folder: Path) -> Path:
    folder = embargo_root(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ensure_not_open_access_folder(output_folder: Path) -> Path:
    folder = not_open_access_root(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
