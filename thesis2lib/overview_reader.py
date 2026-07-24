"""Overview workbook reader for SOP archive metadata."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openpyxl import load_workbook

from thesis2lib.constants import (
    DIRECT_GRADING_COLUMNS,
    GRADING_FIELD_PATTERNS,
    GRADING_FIRST_SHEET,
    GRADING_SECOND_SHEET,
    MATCHING_SHEET_PREFIX,
    OVERVIEW_IDENTITY_COLUMNS,
)

HEADER_SCAN_ROWS = 8
from thesis2lib.canvas_attachments import attachment_label
from thesis2lib.models import ArchiveMetadata, OverviewWorkbook, PdfSelection, SubmissionRecord
from thesis2lib.utils import (
    clean_text,
    format_course_final_grade,
    format_grade_display,
    normalize_identifier,
    normalize_reader_overall_grade,
    parse_score,
)

INDIVIDUAL_SCORE_PREFIXES = ("firstreader_", "secondreader_")
INDIVIDUAL_SCORE_SKIP_SUFFIXES = ("_comm", "_comments", "_comment")

REQUIRED_ARCHIVE_FIELDS = (
    "student_name",
    "sis_user_id",
    "supervisor_name",
    "second_reader_name",
    "submission_date",
    "final_grade",
)


def normalize_header(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).casefold())


def matching_sheet_name(semester: str) -> str:
    return f"{MATCHING_SHEET_PREFIX}{semester}"


def resolve_sheet_name(workbook_sheets: list[str], expected: str) -> str:
    if expected in workbook_sheets:
        return expected
    expected_lower = expected.casefold()
    for sheet in workbook_sheets:
        if sheet.casefold() == expected_lower:
            return sheet
    raise ValueError(f"Overview workbook is missing required sheet: {expected}")


def find_identity_column(columns: Iterable[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {normalize_header(column): column for column in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def find_grading_column(
    columns: Iterable[str],
    attempt: str,
    field_name: str,
) -> str | None:
    attempt_token = "1st submission" if attempt == "First" else "2nd submission"
    patterns = GRADING_FIELD_PATTERNS[field_name]
    best_match: str | None = None

    for column in columns:
        header = normalize_header(column)
        if attempt_token not in header:
            continue
        if any(pattern in header for pattern in patterns):
            return column
        if field_name == "supervisor_name" and "supervisor" in header:
            best_match = best_match or column

    return best_match


def map_grading_columns(columns: list[str], attempt: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for field_name in OVERVIEW_IDENTITY_COLUMNS:
        column = find_identity_column(columns, OVERVIEW_IDENTITY_COLUMNS[field_name])
        if column:
            mapping[field_name] = column

    for field_name in GRADING_FIELD_PATTERNS:
        column = find_grading_column(columns, attempt, field_name)
        if column:
            mapping[field_name] = column

    for field_name, aliases in DIRECT_GRADING_COLUMNS.get(attempt, {}).items():
        if field_name in mapping:
            continue
        column = find_identity_column(columns, aliases)
        if column:
            mapping[field_name] = column

    return mapping


def header_row_has_identifiers(row_values: Iterable[Any]) -> bool:
    normalized = {
        normalize_header(value) for value in row_values if clean_text(value)
    }
    if "snr" in normalized:
        return True
    for aliases in OVERVIEW_IDENTITY_COLUMNS.values():
        if any(alias in normalized for alias in aliases):
            return True
    return False


def detect_header_row(path: Path, sheet_name: str) -> int:
    preview = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=None,
        nrows=HEADER_SCAN_ROWS,
        dtype=str,
        keep_default_na=False,
    )
    for idx in range(len(preview)):
        if header_row_has_identifiers(preview.iloc[idx].tolist()):
            return idx
    return 0


def read_overview_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    header_row = detect_header_row(path, sheet_name)
    df = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=header_row,
        dtype=str,
        keep_default_na=False,
    )
    df.columns = [clean_text(column) for column in df.columns]
    return df


def row_to_record(row: pd.Series, column_map: dict[str, str]) -> dict[str, str]:
    record: dict[str, str] = {}
    for column in row.index:
        header = clean_text(column)
        if header:
            record[header] = clean_text(row.get(column))
    for field_name, column in column_map.items():
        record[field_name] = clean_text(row.get(column))
    if not record.get("student_name"):
        first = record.get("student_first_name", "")
        last = record.get("student_last_name", "")
        combined = clean_text(f"{first} {last}")
        if combined:
            record["student_name"] = combined
    return record


def count_individual_criterion_scores(record: dict[str, str]) -> tuple[int, int]:
    first_reader = 0
    second_reader = 0
    for key, value in record.items():
        normalized = normalize_header(key).replace(" ", "")
        if not any(normalized.startswith(prefix) for prefix in INDIVIDUAL_SCORE_PREFIXES):
            continue
        if any(normalized.endswith(suffix) for suffix in INDIVIDUAL_SCORE_SKIP_SUFFIXES):
            continue
        if parse_score(value) is None:
            continue
        if normalized.startswith("firstreader_"):
            first_reader += 1
        elif normalized.startswith("secondreader_"):
            second_reader += 1
    return first_reader, second_reader


def individual_overall_grade(record: dict[str, str], attempt: str) -> str:
    mapped = clean_text(record.get("final_grade", ""))
    if mapped:
        return normalize_reader_overall_grade(mapped)
    if attempt == "First":
        keys = ("OverallGradeFirst", "RoundedFirst")
    else:
        keys = ("OverallGradeSecond", "RoundedSecond")
    for key in keys:
        value = clean_text(record.get(key, ""))
        if value:
            return normalize_reader_overall_grade(value)
    return ""


def summarize_individual_grades(
    record: dict[str, str] | None,
    attempt: str | None,
) -> tuple[str, int, int, int]:
    if not record or not attempt:
        return "", 0, 0, 0
    first_reader, second_reader = count_individual_criterion_scores(record)
    overall = individual_overall_grade(record, attempt)
    return overall, first_reader, second_reader, first_reader + second_reader


def reader_overall_grades(record: dict[str, str] | None) -> tuple[str, str]:
    """Return first-reader and second-reader overall grades from an overview row."""
    if not record:
        return "", ""

    def pick(*keys: str) -> str:
        for key in keys:
            value = clean_text(record.get(key, ""))
            if value:
                return value
        return ""

    return (
        normalize_reader_overall_grade(pick("RoundedFirst", "OverallGradeFirst")),
        normalize_reader_overall_grade(pick("RoundedSecond", "OverallGradeSecond")),
    )


def index_records(
    df: pd.DataFrame,
    column_map: dict[str, str],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    by_sis: dict[str, dict[str, str]] = {}
    by_canvas: dict[str, dict[str, str]] = {}

    for _, row in df.iterrows():
        record = row_to_record(row, column_map)
        sis_id = normalize_identifier(record.get("sis_user_id", ""))
        canvas_id = normalize_identifier(record.get("canvas_id", ""))
        if sis_id:
            by_sis[sis_id] = record
        if canvas_id:
            by_canvas[canvas_id] = record

    return by_sis, by_canvas


def load_grading_sheet(
    path: Path,
    sheet_name: str,
    attempt: str,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    df = read_overview_sheet(path, sheet_name)
    column_map = map_grading_columns(list(df.columns), attempt)
    required = ("sis_user_id", "canvas_id")
    if not any(column_map.get(name) for name in required):
        raise ValueError(
            f"Sheet '{sheet_name}' is missing student identifier columns (ID or SIS User ID)."
        )
    return index_records(df, column_map)


def load_matching_sheet(
    path: Path,
    sheet_name: str,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    df = read_overview_sheet(path, sheet_name)
    column_map: dict[str, str] = {}
    for field_name, aliases in OVERVIEW_IDENTITY_COLUMNS.items():
        column = find_identity_column(df.columns, aliases)
        if column:
            column_map[field_name] = column

    if not column_map.get("sis_user_id") and not column_map.get("canvas_id"):
        raise ValueError(
            f"Sheet '{sheet_name}' is missing student identifier columns."
        )
    return index_records(df, column_map)


def validate_overview_workbook(path: Path, semester: str) -> None:
    workbook = load_workbook(path, read_only=True)
    sheet_names = workbook.sheetnames
    workbook.close()

    matching_name = resolve_sheet_name(sheet_names, matching_sheet_name(semester))
    resolve_sheet_name(sheet_names, GRADING_FIRST_SHEET)
    resolve_sheet_name(sheet_names, GRADING_SECOND_SHEET)

    load_matching_sheet(path, matching_name)
    load_grading_sheet(path, GRADING_FIRST_SHEET, "First")
    load_grading_sheet(path, GRADING_SECOND_SHEET, "Second")


def load_overview_workbook(path: Path, semester: str) -> OverviewWorkbook:
    workbook = load_workbook(path, read_only=True)
    sheet_names = workbook.sheetnames
    workbook.close()

    matching_name = resolve_sheet_name(sheet_names, matching_sheet_name(semester))
    matching_by_sis, matching_by_canvas = load_matching_sheet(path, matching_name)
    grading_first_sis, grading_first_canvas = load_grading_sheet(
        path, GRADING_FIRST_SHEET, "First"
    )
    grading_second_sis, grading_second_canvas = load_grading_sheet(
        path, GRADING_SECOND_SHEET, "Second"
    )

    return OverviewWorkbook(
        matching_sheet=matching_name,
        grading_first_by_sis=grading_first_sis,
        grading_first_by_canvas=grading_first_canvas,
        grading_second_by_sis=grading_second_sis,
        grading_second_by_canvas=grading_second_canvas,
        matching_by_sis=matching_by_sis,
        matching_by_canvas=matching_by_canvas,
    )


def normalize_student_name(value: Any) -> str:
    text = clean_text(value).casefold()
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        if first and last:
            text = f"{first} {last}"
    return re.sub(r"\s+", " ", text).strip()


def overview_snr_from_pdf_selection(pdf_selection: PdfSelection | None) -> str:
    if not pdf_selection:
        return ""
    names: list[str] = []
    if pdf_selection.selected_path is not None:
        names.append(pdf_selection.selected_path.name)
    names.extend(path.name for path in pdf_selection.candidates)
    if pdf_selection.selected_attachment:
        names.append(attachment_label(pdf_selection.selected_attachment))
    for attachment in pdf_selection.candidate_attachments:
        names.append(attachment_label(attachment))
    for name in names:
        match = re.search(r"(?<!\d)(\d{7})(?!\d)", name)
        if match:
            return match.group(1)
    return ""


def normalize_name_part(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).casefold()).strip()


def parse_student_name_parts(name: str) -> tuple[str, str]:
    """Parse Canvas-style names such as 'Aberson, Niels' into first and last."""
    text = clean_text(name)
    if not text:
        return "", ""
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        return normalize_name_part(first), normalize_name_part(last)
    parts = text.split()
    if len(parts) >= 2:
        return normalize_name_part(parts[0]), normalize_name_part(" ".join(parts[1:]))
    return normalize_name_part(text), ""


def overview_full_name(record: dict[str, str]) -> str:
    parts = [
        normalize_name_part(record.get("student_first_name", "")),
        normalize_name_part(record.get("student_last_name", "")),
    ]
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


TURKISH_CHAR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)


def strip_diacritics(value: str) -> str:
    translated = value.translate(TURKISH_CHAR_MAP)
    normalized = unicodedata.normalize("NFKD", translated)
    return normalized.encode("ascii", "ignore").decode("ascii")


def fold_ascii_name(value: str) -> str:
    normalized = normalize_name_part(value)
    folded = strip_diacritics(normalized)
    return re.sub(r"\s+", " ", folded).strip()


def first_name_matches(canvas_first: str, record_first: str) -> bool:
    canvas_first = normalize_name_part(canvas_first)
    record_first = normalize_name_part(record_first)
    if not canvas_first or not record_first:
        return False
    if canvas_first == record_first:
        return True

    record_tokens = record_first.split()
    if canvas_first in record_tokens:
        return True

    parenthetical = re.search(r"\(([^)]+)\)", record_first)
    if parenthetical and fold_ascii_name(parenthetical.group(1)) == fold_ascii_name(canvas_first):
        return True

    for token in record_tokens:
        if len(token) >= 3 and (
            canvas_first == token
            or canvas_first in token
            or token in canvas_first
        ):
            return True
    return False


def names_match_record(student_name: str, record: dict[str, str]) -> bool:
    overview_name = overview_full_name(record)
    canvas_name = normalize_student_name(student_name)

    if overview_name and canvas_name:
        if overview_name == canvas_name:
            return True
        if fold_ascii_name(overview_name) == fold_ascii_name(canvas_name):
            return True

    canvas_first, canvas_last = parse_student_name_parts(student_name)
    record_first = normalize_name_part(record.get("student_first_name", ""))
    record_last = normalize_name_part(record.get("student_last_name", ""))
    if record_first and record_last and canvas_first and canvas_last:
        if first_name_matches(canvas_first, record_first) and fold_ascii_name(
            canvas_last
        ) == fold_ascii_name(record_last):
            return True

    return False


def find_record_by_name(
    records: dict[str, dict[str, str]],
    student_name: str,
) -> dict[str, str] | None:
    if not clean_text(student_name):
        return None
    for record in records.values():
        if names_match_record(student_name, record):
            return record
    return None


def student_name_from_overview_record(record: dict[str, str] | None) -> str:
    """Return the student display name from an overview matching or grading row."""
    if not record:
        return ""
    name = clean_text(record.get("student_name", ""))
    if name:
        return name
    first = clean_text(record.get("student_first_name", ""))
    last = clean_text(record.get("student_last_name", ""))
    combined = clean_text(f"{first} {last}".strip())
    if combined:
        return combined
    return ""


def lookup_overview_student_name(
    overview: OverviewWorkbook | None,
    *,
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str = "",
    canvas_name: str = "",
) -> str:
    """Look up the programme student name from the overview workbook."""
    if overview is None:
        return ""

    snr = normalize_identifier(overview_snr)
    sis = normalize_identifier(sis_user_id)
    canvas = normalize_identifier(canvas_id)
    lookups = [
        lookup_matching_record(overview, sis, canvas, snr, canvas_name),
        lookup_grading_record(overview, "Second", sis, canvas, snr, canvas_name),
        lookup_grading_record(overview, "First", sis, canvas, snr, canvas_name),
    ]
    for record in lookups:
        name = student_name_from_overview_record(record)
        if name:
            return name
    return ""


def lookup_overview_snr(
    overview: OverviewWorkbook,
    sis_user_id: str,
    canvas_id: str,
    student_name: str,
) -> str:
    """Cross-reference the overview matching sheet to find the programme SNR."""
    matching = lookup_matching_record(
        overview,
        sis_user_id,
        canvas_id,
        "",
        student_name,
    )
    if not matching:
        return ""
    return normalize_identifier(matching.get("SNR") or matching.get("sis_user_id", ""))


def resolve_overview_snr(
    overview: OverviewWorkbook | None,
    sis_user_id: str,
    canvas_id: str,
    student_name: str,
    pdf_snr: str = "",
) -> str:
    snr = normalize_identifier(pdf_snr)
    if snr:
        return snr
    if overview is None:
        return ""
    return lookup_overview_snr(overview, sis_user_id, canvas_id, student_name)


def overview_has_snr(overview: OverviewWorkbook, snr: str) -> bool:
    """Return True when the SNR appears in the overview workbook indexes."""
    normalized = normalize_identifier(snr)
    if not normalized:
        return False
    return (
        normalized in overview.matching_by_sis
        or normalized in overview.grading_first_by_sis
        or normalized in overview.grading_second_by_sis
    )


def resolve_overview_record(
    by_sis: dict[str, dict[str, str]],
    by_canvas: dict[str, dict[str, str]],
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str = "",
    student_name: str = "",
) -> dict[str, str] | None:
    if overview_snr and overview_snr in by_sis:
        return by_sis[overview_snr]
    if sis_user_id and sis_user_id in by_sis:
        return by_sis[sis_user_id]
    if canvas_id and canvas_id in by_canvas:
        return by_canvas[canvas_id]
    return find_record_by_name(by_sis, student_name)


def enrollment_matches_overview(
    overview: OverviewWorkbook,
    enrollment: dict,
) -> bool:
    user = enrollment.get("user") or {}
    sis_user_id = normalize_identifier(user.get("sis_user_id"))
    canvas_id = normalize_identifier(user.get("id") or enrollment.get("user_id"))
    name = clean_text(user.get("sortable_name") or user.get("name"))

    for attempt in ("First", "Second"):
        if lookup_grading_record(
            overview,
            attempt,
            sis_user_id,
            canvas_id,
            "",
            name,
        ):
            return True
    return lookup_matching_record(
        overview,
        sis_user_id,
        canvas_id,
        "",
        name,
    ) is not None


def filter_enrollments_by_overview(
    enrollments: list[dict],
    overview: OverviewWorkbook,
) -> list[dict]:
    """Keep only enrollments that appear in the programme overview workbook."""
    return [
        enrollment
        for enrollment in enrollments
        if enrollment_matches_overview(overview, enrollment)
    ]


def lookup_grading_record(
    overview: OverviewWorkbook,
    attempt: str,
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str = "",
    student_name: str = "",
) -> dict[str, str] | None:
    if attempt == "First":
        by_sis = overview.grading_first_by_sis
        by_canvas = overview.grading_first_by_canvas
    else:
        by_sis = overview.grading_second_by_sis
        by_canvas = overview.grading_second_by_canvas

    return resolve_overview_record(
        by_sis,
        by_canvas,
        sis_user_id,
        canvas_id,
        overview_snr,
        student_name,
    )


def lookup_matching_record(
    overview: OverviewWorkbook,
    sis_user_id: str,
    canvas_id: str,
    overview_snr: str = "",
    student_name: str = "",
) -> dict[str, str] | None:
    return resolve_overview_record(
        overview.matching_by_sis,
        overview.matching_by_canvas,
        sis_user_id,
        canvas_id,
        overview_snr,
        student_name,
    )


def lookup_individual_assessment_from_overview(
    overview: OverviewWorkbook,
    final_attempt: str | None,
    sis_user_id: str,
    canvas_id: str,
    student_name: str = "",
    overview_snr: str = "",
) -> tuple[bool, bool, bool]:
    if not final_attempt:
        return False, False, False

    record = lookup_grading_record(
        overview,
        final_attempt,
        sis_user_id,
        canvas_id,
        overview_snr,
        student_name,
    )
    if not record:
        return False, False, False

    supervisor_name = clean_text(record.get("supervisor_name", ""))
    second_reader_name = clean_text(record.get("second_reader_name", ""))
    overall, first_scores, second_scores, _ = summarize_individual_grades(
        record, final_attempt
    )
    has_grades = bool(overall) or first_scores > 0 or second_scores > 0
    if not has_grades and not supervisor_name and not second_reader_name:
        return False, False, False
    return True, bool(supervisor_name), bool(second_reader_name)


def archive_is_complete(record: dict[str, str]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for field_name in REQUIRED_ARCHIVE_FIELDS:
        if not clean_text(record.get(field_name, "")):
            missing.append(field_name)
    return not missing, missing


def format_submission_date(value: str | None) -> str:
    text = clean_text(value or "")
    if not text:
        return ""
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10]


def format_official_grade(score: float | None) -> str:
    from thesis2lib.utils import format_gradebook_score_ten_scale

    return format_gradebook_score_ten_scale(score)


def enrich_archive_metadata(
    metadata: ArchiveMetadata | None,
    *,
    final_submission: SubmissionRecord | None,
    student_name: str,
    sis_user_id: str,
    canvas_id: str,
    section: str,
    individual_overall_grade: str = "",
    final_score: float | None = None,
    override_score: float | None = None,
    joint_grade: str = "",
) -> ArchiveMetadata | None:
    """Fill archive gaps from Canvas and computed grades, then re-check completeness."""
    if metadata is None:
        return None

    if not metadata.student_name and student_name:
        metadata.student_name = student_name
    if not metadata.sis_user_id:
        metadata.sis_user_id = normalize_identifier(sis_user_id)
    if not metadata.canvas_id:
        metadata.canvas_id = normalize_identifier(canvas_id)
    if not metadata.section and section:
        metadata.section = section

    if not metadata.submission_date and final_submission and final_submission.submitted_at:
        metadata.submission_date = format_submission_date(final_submission.submitted_at)

    if override_score is not None:
        metadata.final_grade = format_official_grade(override_score)
    elif final_score is not None:
        from thesis2lib.scoring import derive_override_score_from_final

        metadata.final_grade = format_official_grade(
            derive_override_score_from_final(final_score)
        )

    record = {
        "student_name": metadata.student_name,
        "sis_user_id": metadata.sis_user_id,
        "supervisor_name": metadata.supervisor_name,
        "second_reader_name": metadata.second_reader_name,
        "submission_date": metadata.submission_date,
        "final_grade": metadata.final_grade,
    }
    complete, missing = archive_is_complete(record)
    metadata.complete = complete
    metadata.missing_fields = missing
    return metadata


def lookup_archive_metadata(
    overview: OverviewWorkbook | None,
    final_attempt: str | None,
    sis_user_id: str,
    canvas_id: str,
    fallback_name: str = "",
    fallback_section: str = "",
    overview_snr: str = "",
) -> ArchiveMetadata | None:
    if not overview or not final_attempt:
        return None

    grading_record = lookup_grading_record(
        overview,
        final_attempt,
        sis_user_id,
        canvas_id,
        overview_snr,
        fallback_name,
    )
    matching_record = lookup_matching_record(
        overview,
        sis_user_id,
        canvas_id,
        overview_snr,
        fallback_name,
    )

    if not grading_record and not matching_record:
        return ArchiveMetadata(
            student_name=fallback_name,
            canvas_id=canvas_id,
            sis_user_id=sis_user_id,
            sis_login_id="",
            section=fallback_section,
            supervisor_name="",
            second_reader_name="",
            third_reader_name="",
            submission_date="",
            final_grade="",
            thesis_title="",
            source_sheet=(
                GRADING_FIRST_SHEET
                if final_attempt == "First"
                else GRADING_SECOND_SHEET
            ),
            found=False,
            complete=False,
            missing_fields=list(REQUIRED_ARCHIVE_FIELDS),
        )

    merged: dict[str, str] = {}
    if matching_record:
        merged.update(matching_record)
    if grading_record:
        merged.update({key: value for key, value in grading_record.items() if value})

    if not merged.get("student_name"):
        merged["student_name"] = fallback_name
    if not merged.get("section"):
        merged["section"] = fallback_section
    if not merged.get("canvas_id"):
        merged["canvas_id"] = canvas_id
    if overview_snr:
        merged["sis_user_id"] = overview_snr
    elif not merged.get("sis_user_id"):
        merged["sis_user_id"] = sis_user_id

    complete, missing = archive_is_complete(merged)
    return ArchiveMetadata(
        student_name=merged.get("student_name", ""),
        canvas_id=normalize_identifier(merged.get("canvas_id", "")),
        sis_user_id=normalize_identifier(merged.get("sis_user_id", "")),
        sis_login_id=merged.get("sis_login_id", ""),
        section=merged.get("section", ""),
        supervisor_name=merged.get("supervisor_name", ""),
        second_reader_name=merged.get("second_reader_name", ""),
        third_reader_name=merged.get("third_reader_name", ""),
        submission_date=merged.get("submission_date", ""),
        final_grade=merged.get("final_grade", ""),
        thesis_title=merged.get("thesis_title", ""),
        source_sheet=(
            GRADING_FIRST_SHEET
            if final_attempt == "First"
            else GRADING_SECOND_SHEET
        ),
        found=True,
        complete=complete,
        missing_fields=missing,
    )
