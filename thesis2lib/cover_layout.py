"""Shared cover-page parsing helpers for thesis title extraction."""

from __future__ import annotations

import re

from thesis2lib.text_polish import (
    denoise_spaced_text,
    fix_compound_terms,
    looks_like_spaced_letters,
    normalize_unicode,
    polish_thesis_title,
    strip_ocr_noise,
)
from thesis2lib.utils import clean_text

MAX_TITLE_CHARS = 280
MAX_METADATA_ANCHOR_LINES = 6

REVISION_TITLE = re.compile(
    r"(?:"
    r"revise\s+document|"
    r"revision\s+document|"
    r"summary\s+of\s+revisions|"
    r"changes\s+thesis|"
    r"based\s+on\s+the\s+feedback"
    r")",
    re.I,
)

NUMBERED_LIST_ITEM = re.compile(r"\b\d+\.\s")

COMMENT_RESPONSE = re.compile(
    r"(?:"
    r"\bcomments?\s*:|"
    r"\brespond\s*:|"
    r"\bresponse\s+to\s+(?:feedback|comments|reviewers)|"
    r"\bintroduction\s+\d+\.\s*comments?\b|"
    r"\byou\s+explain\b|"
    r"\bformer\s+research\b"
    r")",
    re.I,
)

PROGRAMME_HEADER = re.compile(
    r"(?:"
    r"data\s+science\s*(?:&|and)\s*society\s*[-–]?\s*thesis|"
    r"thesis\s*[-–]\s*feedback|"
    r"^master\s+thesis\s*$|"
    r"^master\s+of\s+science\s*$"
    r")",
    re.I,
)

def looks_like_comment_response_content(text: str) -> bool:
    if not text:
        return False
    if COMMENT_RESPONSE.search(text):
        return True
    if len(NUMBERED_LIST_ITEM.findall(text)) >= 2 and "comment" in text.lower():
        return True
    return False


def looks_like_programme_header(text: str) -> bool:
    return bool(PROGRAMME_HEADER.search(clean_text(text)))


STRONG_LAYOUT_METHODS = frozenset(
    {
        "cover_layout",
        "cover_layout_name",
        "cover_layout_caps_author",
    }
)

WEAK_EXTRACTION_METHODS = frozenset(
    {
        "metadata_anchor",
        "flat_cover",
        "pdf_metadata",
        "none",
    }
)

SUBMIT_ANCHOR = re.compile(r"thesis\s+submitted", re.I)

REVISION_PAGE = re.compile(
    r"(?:"
    r"revision\s+(?:document|form|report|overview)|"
    r"response\s+to\s+(?:feedback|comments|reviewers)|"
    r"summary\s+of\s+(?:revisions|changes)|"
    r"list\s+of\s+changes"
    r")",
    re.I,
)

RESPONSE_SECTION = re.compile(
    r"^(?:introduction|methodology|literature review)\s*:?\s*$",
    re.I,
)

METADATA_LINE = re.compile(
    r"(?:"
    r"thesis\s+submitted|"
    r"partial\s+fulfillment|"
    r"requirements\s+for\s+the\s+degree|"
    r"master\s+of\s+science|"
    r"master\s+thesis|"
    r"data\s+science\s*[&]?\s*society|"
    r"school\s+of\s+humanities|"
    r"digital\s+sciences|"
    r"tilburg\s+university|"
    r"department\s+of|"
    r"faculty\s+of|"
    r"supervisor|"
    r"second\s+reader|"
    r"third\s+reader|"
    r"student\s+number|"
    r"\bsnr\b|"
    r"^\s*(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}\s*$"
    r")",
    re.I,
)

HEADER_LINE = re.compile(
    r"(?:"
    r"^\s*\d{1,3}\s*$|"
    r"tilburg\s+university|"
    r"tshd|"
    r"^\s*logo\s*$"
    r")",
    re.I,
)

READER_SUPERVISOR_LINE = re.compile(r"^\s*(supervisor|second\s+reader|third\s+reader)\s*:", re.I)

GENERIC_METADATA_TITLES = {
    "",
    "untitled",
    "document",
    "microsoft word",
    "microsoft word - ",
}


def normalize_cover_line(text: str) -> str:
    """Light per-line cleanup; full title polish happens after layout assembly."""
    text = normalize_unicode(clean_text(text))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if looks_like_spaced_letters(text):
        return denoise_spaced_text(text)
    return fix_compound_terms(text)


def normalize_cover_lines(raw_lines: list[str]) -> list[str]:
    return [line for line in (normalize_cover_line(text) for text in raw_lines) if line]


def _name_tokens(student_name: str) -> set[str]:
    tokens: set[str] = set()
    for part in re.split(r"[\s,]+", clean_text(student_name).lower()):
        if len(part) > 2:
            tokens.add(part)
    return tokens


def _compact_name(text: str) -> str:
    return re.sub(r"[\s,.\-']+", "", clean_text(text).lower())


def line_matches_name(line: str, student_name: str) -> bool:
    tokens = _name_tokens(student_name)
    if not tokens:
        return False

    candidates = [
        line.lower(),
        denoise_spaced_text(line).lower(),
        re.sub(r"\s+", "", denoise_spaced_text(line).lower()),
    ]
    for candidate in candidates:
        hits = sum(1 for token in tokens if token in candidate)
        required = 2 if len(tokens) >= 2 else 1
        if hits >= required:
            return True

    name_compact = _compact_name(student_name)
    for candidate in candidates:
        if name_compact and name_compact in _compact_name(candidate):
            return True
    return False


def is_header_line(line: str) -> bool:
    if not line:
        return True
    if HEADER_LINE.search(line):
        return True
    if len(line) <= 2:
        return True
    lower = line.lower()
    if "tilburg" in lower and len(line) < 50:
        return True
    if re.search(r"\buniversity\b", lower) and len(line) < 50 and "submitted" not in lower:
        return True
    return False


def is_metadata_line(line: str) -> bool:
    if not line:
        return True
    if METADATA_LINE.search(line):
        return True
    if READER_SUPERVISOR_LINE.search(line):
        return True
    return False


def looks_like_author_line(line: str, student_name: str) -> bool:
    if is_metadata_line(line) or is_header_line(line):
        return False
    if line_matches_name(line, student_name):
        return True
    words = line.split()
    if 2 <= len(words) <= 6 and line.upper() == line and not any(char.isdigit() for char in line):
        return True
    return False


def is_valid_title(title: str) -> bool:
    text = clean_text(title)
    if len(text) < 12:
        return False
    if is_metadata_line(text):
        return False
    lower = text.lower()
    if "school of humanities" in lower or "digital sciences" in lower:
        return False
    if READER_SUPERVISOR_LINE.search(text):
        return False
    return True


def is_plausible_thesis_title(title: str) -> bool:
    """Reject revision letters, comment responses, and other non-title cover blobs."""
    if not is_valid_title(title):
        return False
    if len(title) > MAX_TITLE_CHARS:
        return False
    if REVISION_TITLE.search(title):
        return False
    if looks_like_comment_response_content(title):
        return False
    if looks_like_programme_header(title):
        return False
    if len(NUMBERED_LIST_ITEM.findall(title)) >= 2:
        return False
    return True


def join_title_lines(lines: list[str]) -> str:
    deduped: list[str] = []
    for line in lines:
        if deduped and deduped[-1].casefold() == line.casefold():
            continue
        deduped.append(line)
    return polish_thesis_title(" ".join(deduped))


def metadata_start_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if is_metadata_line(line):
            return index
    return len(lines)


def find_author_index(lines: list[str], student_name: str, *, before: int) -> int | None:
    for index in range(before - 1, -1, -1):
        if looks_like_author_line(lines[index], student_name):
            return index
    return None


def title_start_index(lines: list[str], author_idx: int) -> int:
    start = 0
    while start < author_idx and (is_header_line(lines[start]) or is_metadata_line(lines[start])):
        start += 1
    return start


def is_title_line(line: str) -> bool:
    if is_header_line(line) or is_metadata_line(line):
        return False
    if looks_like_comment_response_content(line):
        return False
    if looks_like_programme_header(line):
        return False
    cleaned = strip_ocr_noise(line)
    if not cleaned or len(cleaned) < 4:
        return False
    return True


def looks_like_revision_document_page(lines: list[str]) -> bool:
    if not lines:
        return False
    early_text = " ".join(lines[:12])
    if REVISION_PAGE.search(early_text):
        return True
    if looks_like_comment_response_content(early_text):
        return True
    if len(lines) >= 2 and RESPONSE_SECTION.match(lines[1]):
        return True
    return False


def title_from_cover_layout(lines: list[str], student_name: str) -> str:
    title, _, _ = title_from_cover_layout_with_confidence(lines, student_name)
    return title


def title_from_cover_layout_with_confidence(
    lines: list[str],
    student_name: str,
) -> tuple[str, str, str]:
    """Cover layout: logo/header, title block, author name, then metadata."""
    metadata_start = metadata_start_index(lines)
    author_idx = find_author_index(lines, student_name, before=metadata_start)

    if author_idx is not None and author_idx > 0:
        start = title_start_index(lines, author_idx)
        title_lines = [
            line
            for line in lines[start:author_idx]
            if is_title_line(line)
        ]
        title = join_title_lines(title_lines)
        if is_plausible_thesis_title(title):
            name_matched = line_matches_name(lines[author_idx], student_name)
            if metadata_start < len(lines) and name_matched:
                return title, "High", "cover_layout"
            if name_matched:
                return title, "Medium", "cover_layout_name"
            return title, "Medium", "cover_layout_caps_author"
    return "", "", ""


def strip_author_suffix(text: str, student_name: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    name = clean_text(student_name)
    variants = [name.replace(",", "")]
    if "," in name:
        last, first = (part.strip() for part in name.split(",", 1))
        variants.extend([f"{first} {last}", f"{last} {first}"])

    upper = text.upper()
    for variant in variants:
        candidate = variant.upper().strip()
        if candidate and upper.endswith(candidate):
            return text[: -len(candidate)].strip(" :-")
    return text


def title_from_flat_cover(lines: list[str], student_name: str) -> str:
    for line in lines[:3]:
        match = SUBMIT_ANCHOR.search(line)
        if not match:
            continue
        before_submit = line[: match.start()].strip()
        title = strip_author_suffix(before_submit, student_name)
        if is_plausible_thesis_title(title):
            return polish_thesis_title(title)
    return ""


def title_from_cover_lines(lines: list[str], student_name: str) -> str:
    title, _, _ = title_from_cover_lines_with_confidence(lines, student_name)
    return title


def title_from_metadata_anchor(lines: list[str]) -> tuple[str, str, str]:
    """Last-resort fallback; skipped for comment/revision pages."""
    if looks_like_comment_response_content(" ".join(lines[:15])):
        return "", "", ""
    metadata_start = metadata_start_index(lines)
    title_lines = [
        line
        for line in lines[:metadata_start]
        if is_title_line(line) and not looks_like_author_line(line, "")
    ]
    if not title_lines or len(title_lines) > 4:
        return "", "", ""
    title = join_title_lines(title_lines)
    if is_plausible_thesis_title(title):
        return title, "Low", "metadata_anchor"
    return "", "", ""


def title_from_cover_lines_with_confidence(
    lines: list[str],
    student_name: str,
) -> tuple[str, str, str]:
    if looks_like_revision_document_page(lines):
        return "", "None", "revision_document"

    title, confidence, method = title_from_cover_layout_with_confidence(lines, student_name)
    if title:
        return title, confidence, method

    title, confidence, method = title_from_metadata_anchor(lines)
    if title:
        return title, confidence, method

    title = title_from_flat_cover(lines, student_name)
    if title:
        return title, "Medium", "flat_cover"

    return "", "None", "none"
