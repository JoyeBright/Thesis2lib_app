"""General text and path utilities."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

from thesis2lib.constants import BLANK_MARKERS, INVALID_FILENAME_CHARS, SUBMISSION_SCORE_MAX

LOGGER = logging.getLogger(__name__)

# Same pattern as openpyxl uses for worksheet cell values.
EXCEL_ILLEGAL_CHARACTERS_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")
EXCEL_MAX_CELL_LENGTH = 32767


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def sanitize_for_excel(value: Any) -> Any:
    """Remove characters Excel rejects while keeping the readable text."""
    if value is None:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return value
        return value

    text = value if isinstance(value, str) else str(value)
    cleaned = EXCEL_ILLEGAL_CHARACTERS_RE.sub("", text)
    if len(cleaned) > EXCEL_MAX_CELL_LENGTH:
        LOGGER.warning(
            "Excel cell value truncated from %s to %s characters.",
            len(cleaned),
            EXCEL_MAX_CELL_LENGTH,
        )
        cleaned = cleaned[: EXCEL_MAX_CELL_LENGTH - 1] + "…"
    return cleaned


def excel_safe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: sanitize_for_excel(val) for key, val in row.items()} for row in records]


def parse_score(value: Any) -> float | None:
    """Convert Canvas or spreadsheet score values to float, safely."""
    text = clean_text(value)
    if text.lower() in BLANK_MARKERS:
        return None
    text = text.replace("%", "").replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return None


def round_grade_2dp(value: float) -> float:
    return round(float(value), 2)


def format_grade_2dp(value: float | None) -> str:
    if value is None:
        return ""
    return f"{round_grade_2dp(value):.2f}"


def format_grade_display(value: Any) -> str:
    """Format numeric grades to two decimals; pass through other text."""
    score = parse_score(value)
    if score is None:
        return clean_text(value)
    return format_grade_2dp(score)


def format_course_final_grade(score: float | None) -> str:
    """Course final grade from Override Score on the 0–100 scale."""
    return format_grade_2dp(score)


def format_gradebook_score_ten_scale(score: float | None) -> str:
    """Convert a 0–100 gradebook score to the 0–10 course grade scale."""
    if score is None:
        return ""
    return format_grade_2dp(score / 10.0)


def normalize_reader_overall_grade(raw: Any) -> str:
    """Individual reader overall grades are on 0–60; cap and format to 2 decimals."""
    score = parse_score(raw)
    if score is None:
        return clean_text(raw)
    capped = min(max(score, 0.0), SUBMISSION_SCORE_MAX)
    return format_grade_2dp(capped)


def normalize_identifier(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def sanitize_folder_name(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", clean_text(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Unknown Student"


def student_folder_path(output_root: Path, student_id: str, student_name: str) -> Path:
    return (
        output_root
        / f"{sanitize_folder_name(student_id)} - {sanitize_folder_name(student_name)}"
    )


def list_pdfs(folder: Path) -> list[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"PDF folder not found: {folder}")
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
    )


def should_fetch_download(path: Path, *, overwrite: bool, skip_existing: bool) -> bool:
    """Return True when a Canvas or cache download should run."""
    if not path.is_file():
        return True
    if skip_existing:
        return False
    return overwrite


def download_overwrite(*, overwrite: bool, skip_existing: bool) -> bool:
    """Whether an existing downloaded file may be replaced."""
    return overwrite and not skip_existing
