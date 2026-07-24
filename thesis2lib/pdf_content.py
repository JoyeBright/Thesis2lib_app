"""Validate thesis PDF cover content after selection."""

from __future__ import annotations

import re
from pathlib import Path

from thesis2lib.cover_layout import looks_like_comment_response_content, looks_like_revision_document_page

REVISION_FILENAME = re.compile(
    r"revision\s+(?:document|form|report|overview)|"
    r"response\s+to\s+(?:feedback|comments|reviewers)|"
    r"summary\s+of\s+revisions",
    re.I,
)


def revision_document_filename(name: str) -> bool:
    return bool(REVISION_FILENAME.search(name))


def assess_cover_lines(
    lines: list[str],
    *,
    filename: str = "",
) -> tuple[bool, str, str]:
    """Return (looks_like_thesis, confidence, detail) from pre-read cover lines."""
    if filename and revision_document_filename(filename):
        return False, "Low", "Filename suggests a revision document."

    if not lines:
        return True, "Low", "Cover page text could not be read."

    if looks_like_revision_document_page(lines):
        return False, "None", "Cover page content looks like a revision document."

    if looks_like_comment_response_content(" ".join(lines[:15])):
        return False, "None", "Cover page content looks like a comment-response document."

    if any("thesis submitted" in line.lower() for line in lines[:12]):
        return True, "High", "Cover page contains standard thesis submission text."

    if any(
        token in " ".join(lines[:12]).lower()
        for token in ("master thesis", "data science", "tilburg university")
    ):
        return True, "Medium", "Cover page contains thesis metadata cues."

    return True, "Medium", "Cover page read but layout is non-standard."


def assess_thesis_pdf_content(
    path: Path,
    *,
    filename: str = "",
    lines: list[str] | None = None,
) -> tuple[bool, str, str]:
    """Return (looks_like_thesis, confidence, detail)."""
    if lines is None:
        from thesis2lib.thesis_title_ocr import read_cover_page_lines

        lines = list(read_cover_page_lines(path).lines)
    return assess_cover_lines(lines, filename=filename or path.name)
