"""Extract thesis titles from final submission PDFs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from thesis2lib.cover_layout import (
    GENERIC_METADATA_TITLES,
    STRONG_LAYOUT_METHODS,
    WEAK_EXTRACTION_METHODS,
    is_plausible_thesis_title,
    is_valid_title,
    looks_like_comment_response_content,
    looks_like_revision_document_page,
    normalize_cover_line,
    title_from_cover_lines_with_confidence,
)
from thesis2lib.pdf_content import assess_cover_lines
from thesis2lib.text_polish import polish_thesis_title
from thesis2lib.thesis_title_ocr import read_cover_page_lines

LOGGER = logging.getLogger(__name__)

CONFIDENCE_ORDER = {"High": 3, "Medium": 2, "Low": 1, "Manual": 4, "None": 0}


@dataclass
class TitleExtractionResult:
    title: str
    confidence: str
    method: str
    detail: str
    needs_review: bool = False
    looks_like_thesis: bool = True
    content_confidence: str = ""
    content_detail: str = ""


def _lower_confidence(current: str, candidate: str) -> str:
    if CONFIDENCE_ORDER.get(candidate, 0) < CONFIDENCE_ORDER.get(current, 0):
        return candidate
    return current


def _metadata_title(reader: PdfReader) -> tuple[str, str]:
    meta = reader.metadata
    if meta is None:
        return "", ""
    raw = normalize_cover_line(meta.get("/Title") or getattr(meta, "title", ""))
    if raw.lower().strip(" .") in GENERIC_METADATA_TITLES:
        return "", ""
    polished = polish_thesis_title(raw) if is_valid_title(raw) else ""
    return polished, "Medium" if polished else ""


def _title_from_pdf_metadata(path: Path) -> TitleExtractionResult:
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
        meta_title, meta_confidence = _metadata_title(reader)
        if meta_title:
            return TitleExtractionResult(
                title=meta_title,
                confidence=meta_confidence,
                method="pdf_metadata",
                detail="Extracted from PDF document metadata.",
                needs_review=True,
            )
    except Exception as exc:
        LOGGER.warning("PDF metadata title extraction failed for %s: %s", path.name, exc)
    return TitleExtractionResult("", "None", "none", "PDF metadata did not yield a title.", True)


def _normalize_title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", polish_thesis_title(text).casefold()).strip()


def extracted_title_looks_unreliable(title: str, confidence: str) -> bool:
    """True when an automatic title extraction should still be checked manually."""
    if confidence in {"Manual", "High"}:
        return False
    polished = polish_thesis_title(title or "")
    if not polished or polished.casefold() == "thesis":
        return True
    if not is_plausible_thesis_title(polished):
        return True
    if confidence in {"Low", "None"}:
        return True
    return False


def extracted_title_differs_from_overview(extracted: str, overview: str) -> bool:
    """True when a cover-extracted title should be checked against the overview row."""
    polished_overview = polish_thesis_title(overview) if overview else ""
    current = polish_thesis_title(extracted or "")
    if not polished_overview or not is_valid_title(polished_overview):
        return False
    if not current or current.casefold() == "thesis":
        return True
    return not _titles_are_similar(current, polished_overview)


def _titles_are_similar(left: str, right: str) -> bool:
    a = _normalize_title_key(left)
    b = _normalize_title_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / min(len(a_tokens), len(b_tokens))
    return overlap >= 0.55


def _prefer_overview_title(
    cover_title: str,
    cover_confidence: str,
    overview_title: str,
    *,
    method: str = "",
) -> tuple[str, str, str | None]:
    """Use overview only when the PDF cover did not yield a plausible thesis title."""
    polished_overview = polish_thesis_title(overview_title) if overview_title else ""
    cover_ok = bool(
        cover_title
        and cover_title.casefold() != "thesis"
        and is_plausible_thesis_title(cover_title)
        and not looks_like_comment_response_content(cover_title)
        and method in STRONG_LAYOUT_METHODS
    )
    if cover_ok:
        return cover_title, cover_confidence, None

    if not polished_overview or not is_valid_title(polished_overview):
        return cover_title, cover_confidence, None

    if (
        not cover_title
        or cover_title.casefold() == "thesis"
        or not is_plausible_thesis_title(cover_title)
        or looks_like_comment_response_content(cover_title)
        or method in WEAK_EXTRACTION_METHODS
    ):
        return polished_overview, "Medium", "overview_fallback"

    return cover_title, cover_confidence, None


def _score_title_result(result: TitleExtractionResult) -> int:
    if not result.title or result.title.casefold() == "thesis":
        return -1
    if not result.looks_like_thesis:
        return -1
    if not is_plausible_thesis_title(result.title):
        return -1
    if looks_like_comment_response_content(result.title):
        return -1

    score = CONFIDENCE_ORDER.get(result.confidence, 0) * 1_000
    method = result.method or ""
    if any(token in method for token in STRONG_LAYOUT_METHODS):
        score += 800
    if "metadata_anchor" in method:
        score -= 900
    if "overview" in method:
        score -= 400
    score += min(len(result.title), 220)
    return score


def _extract_from_pdf_path(
    path: Path,
    *,
    student_name: str,
    filename: str,
) -> TitleExtractionResult:
    """Extract a title from one PDF file (cover page only, no overview)."""
    cover = read_cover_page_lines(path, student_name=student_name)
    lines = list(cover.lines)

    looks_like_thesis, pdf_confidence, pdf_detail = assess_cover_lines(
        lines,
        filename=filename or path.name,
    )
    if not looks_like_thesis:
        return TitleExtractionResult(
            "",
            "None",
            "revision_document",
            pdf_detail,
            needs_review=True,
            looks_like_thesis=False,
            content_confidence=pdf_confidence,
            content_detail=pdf_detail,
        )

    if lines and looks_like_revision_document_page(lines):
        return TitleExtractionResult(
            "",
            "None",
            "revision_document",
            "Cover page looks like a revision or comment-response document.",
            needs_review=True,
            looks_like_thesis=False,
            content_confidence="None",
            content_detail="Cover page looks like a revision or comment-response document.",
        )

    if not lines:
        return TitleExtractionResult(
            "",
            "None",
            "none",
            pdf_detail,
            needs_review=True,
            looks_like_thesis=True,
            content_confidence=pdf_confidence,
            content_detail=pdf_detail,
        )

    title, confidence, method = title_from_cover_lines_with_confidence(lines, student_name)
    if not title:
        return TitleExtractionResult(
            "",
            "None",
            "none",
            pdf_detail,
            needs_review=True,
            looks_like_thesis=True,
            content_confidence=pdf_confidence,
            content_detail=pdf_detail,
        )

    source = "pdf_text" if cover.source == "pdf_text" else "ocr"
    detail_method = f"{source}_{method}"
    confidence = _lower_confidence(confidence, pdf_confidence)
    polished = polish_thesis_title(title)
    return TitleExtractionResult(
        title=polished,
        confidence=confidence,
        method=detail_method,
        detail=f"Extracted from cover ({detail_method}). {pdf_detail}",
        needs_review=True,
        looks_like_thesis=True,
        content_confidence=pdf_confidence,
        content_detail=pdf_detail,
    )


def extract_thesis_title_result(
    path: Path,
    *,
    student_name: str = "",
    overview_title: str = "",
    filename: str = "",
    alternate_paths: list[Path] | None = None,
) -> TitleExtractionResult:
    """Extract title from the thesis PDF cover; try alternate PDFs when the first is not a thesis."""
    candidates: list[tuple[Path, str]] = [(path, filename or path.name)]
    seen = {path.resolve()}
    for alt in alternate_paths or []:
        alt_path = alt.resolve()
        if alt_path in seen or not alt_path.is_file():
            continue
        seen.add(alt_path)
        candidates.append((alt_path, alt_path.name))

    best: TitleExtractionResult | None = None
    best_score = -1
    for pdf_path, pdf_name in candidates:
        result = _extract_from_pdf_path(
            pdf_path,
            student_name=student_name,
            filename=pdf_name,
        )
        score = _score_title_result(result)
        if score > best_score:
            best_score = score
            best = result

    if best is None or best_score < 0:
        meta_result = _title_from_pdf_metadata(path)
        if meta_result.title and is_plausible_thesis_title(meta_result.title):
            archive_title = polish_thesis_title(overview_title) if overview_title else ""
            if archive_title and is_valid_title(archive_title):
                return TitleExtractionResult(
                    archive_title,
                    "Low",
                    "overview",
                    "Used thesis title from overview workbook (no thesis PDF cover found).",
                    needs_review=True,
                )
            return meta_result

        archive_title = polish_thesis_title(overview_title) if overview_title else ""
        if archive_title and is_valid_title(archive_title):
            return TitleExtractionResult(
                archive_title,
                "Low",
                "overview",
                "Used thesis title from overview workbook.",
                needs_review=True,
            )
        return TitleExtractionResult(
            "Thesis",
            "None",
            "default",
            "No thesis title could be extracted automatically.",
            needs_review=True,
        )

    layout_method = next(
        (name for name in STRONG_LAYOUT_METHODS if name in (best.method or "")),
        (best.method or "").rsplit("_", 1)[-1],
    )
    title, confidence, overview_method = _prefer_overview_title(
        best.title,
        best.confidence,
        overview_title,
        method=layout_method,
    )
    if overview_method:
        method_label = overview_method
    else:
        method_label = best.method

    needs_review = (
        confidence in {"Low", "None"}
        or best.content_confidence in {"Low", "None"}
        or overview_method == "overview_fallback"
        or method_label in WEAK_EXTRACTION_METHODS
        or extracted_title_looks_unreliable(title, confidence)
    )
    return TitleExtractionResult(
        title=title,
        confidence=confidence,
        method=method_label,
        detail=best.detail if not overview_method else f"Overview workbook title used ({overview_method}).",
        needs_review=needs_review,
        looks_like_thesis=True,
        content_confidence=best.content_confidence,
        content_detail=best.content_detail,
    )


def extract_thesis_title_from_pdf(path: Path, *, student_name: str = "") -> str:
    return extract_thesis_title_result(path, student_name=student_name).title


def extract_title_from_ocr_with_confidence(
    path: Path,
    *,
    student_name: str,
) -> TitleExtractionResult:
    return extract_thesis_title_result(path, student_name=student_name)


def resolve_thesis_title(item, pdf_path: Path) -> str:
    return resolve_thesis_title_result(item, pdf_path).title


def resolve_thesis_title_result(item, pdf_path: Path) -> TitleExtractionResult:
    overview_title = ""
    archive = item.archive_metadata
    if archive and archive.thesis_title:
        overview_title = archive.thesis_title

    selected_name = ""
    pdf = item.pdf_selection
    if pdf is not None:
        if pdf.selected_path is not None:
            selected_name = pdf.selected_path.name
        elif pdf.selected_attachment is not None:
            from thesis2lib.canvas_attachments import attachment_label

            selected_name = attachment_label(pdf.selected_attachment)

    return extract_thesis_title_result(
        pdf_path,
        student_name=item.student.name,
        overview_title=overview_title,
        filename=selected_name,
        alternate_paths=_cached_alternate_paths(item, pdf_path.parent),
    )


def _cached_alternate_paths(item, cache_dir: Path) -> list[Path]:
    pdf = item.pdf_selection
    if pdf is None or not pdf.candidate_attachments:
        return []
    from thesis2lib.canvas_attachments import attachment_path_label
    from thesis2lib.pdf_matching import is_strong_revision_document

    selected_id = str((pdf.selected_attachment or {}).get("id") or "")
    paths: list[Path] = []
    for attachment in pdf.candidate_attachments:
        attachment_id = str(attachment.get("id") or "")
        if not attachment_id or attachment_id == selected_id:
            continue
        if is_strong_revision_document(attachment_path_label(attachment)):
            continue
        alt = cache_dir / f"{item.student.canvas_id}_{attachment_id}.pdf"
        if alt.is_file():
            paths.append(alt)
    return paths
