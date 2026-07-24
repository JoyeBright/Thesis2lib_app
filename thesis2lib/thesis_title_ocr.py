"""OCR-based thesis title extraction from cover page images."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytesseract
from pytesseract import TesseractNotFoundError

from thesis2lib.cover_layout import (
    STRONG_LAYOUT_METHODS,
    looks_like_revision_document_page,
    normalize_cover_line,
    normalize_cover_lines,
    title_from_cover_lines_with_confidence,
)
from thesis2lib.text_polish import looks_like_spaced_letters

LOGGER = logging.getLogger(__name__)

# Bump when OCR pipeline logic changes (invalidates line cache).
OCR_PIPELINE_VERSION = 3

OCR_DPI = 300
OCR_OEM = 1
OCR_TOP_FRACTION = 0.68
OCR_TOP_FRACTION_ALT = 0.78
OCR_PSM_MODES = (4, 6, 11)
OCR_RETRY_SCORE = 18_000

CONFIDENCE_RANK = {"High": 3, "Medium": 2, "Low": 1, "None": 0}


@dataclass(frozen=True)
class CoverPageLines:
    lines: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class _OcrCandidate:
    lines: list[str]
    score: int
    psm: int
    top_fraction: float


def ensure_tesseract_available() -> str:
    """Verify Tesseract is installed; required for thesis title extraction."""
    try:
        return str(pytesseract.get_tesseract_version())
    except TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR is required for thesis title extraction. "
            "Re-run ./setup_conda.sh or install with: "
            "conda install -p .conda -c conda-forge tesseract pytesseract pypdfium2 pillow"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            "Tesseract OCR is required but could not be initialized."
        ) from exc


def pdf_text_cover_lines(path: Path) -> list[str]:
    """Read cover page lines from embedded PDF text (fast)."""
    from pypdf import PdfReader

    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
        if not reader.pages:
            return []
        text = reader.pages[0].extract_text() or ""
        return [normalize_cover_line(line) for line in text.splitlines() if normalize_cover_line(line)]
    except Exception:
        return []
    finally:
        pypdf_logger.setLevel(previous_level)


def render_cover_page_image(path: Path):
    """Render PDF page 1 to a PIL image for OCR."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    if len(pdf) == 0:
        return None
    page = pdf[0]
    bitmap = page.render(scale=OCR_DPI / 72)
    return bitmap.to_pil()


def preprocess_for_ocr(image):
    """Grayscale, autocontrast, and light sharpen before Tesseract."""
    from PIL import ImageFilter, ImageOps

    gray = ImageOps.grayscale(image)
    contrast = ImageOps.autocontrast(gray, cutoff=1)
    return contrast.filter(ImageFilter.SHARPEN)


def crop_title_region(image, *, top_fraction: float):
    width, height = image.size
    crop_height = max(int(height * top_fraction), 1)
    return image.crop((0, 0, width, crop_height))


def _tesseract_config(psm: int) -> str:
    return f"--psm {psm} --oem {OCR_OEM}"


def ocr_image_to_lines(image, *, psm: int) -> list[str]:
    text = pytesseract.image_to_string(image, lang="eng", config=_tesseract_config(psm))
    return normalize_cover_lines(text.splitlines())


def _title_quality_score(title: str, confidence: str, *, method: str = "", source: str = "") -> int:
    if not title or title.casefold() == "thesis":
        return -1
    score = CONFIDENCE_RANK.get(confidence, 0) * 10_000
    score += min(len(title), 240)
    score += title.count(":") * 80
    score += title.count("-") * 30
    score += title.count("'") * 10
    if method in STRONG_LAYOUT_METHODS:
        score += 1_500
    if method == "metadata_anchor":
        score -= 1_200
    if source == "pdf_text" and method in STRONG_LAYOUT_METHODS:
        score += 800
    words = title.split()
    if words:
        last = words[-1]
        if len(last) <= 2:
            score -= 400
        if len(words) >= 2 and len(words[-2]) <= 4 and len(last) <= 3:
            score -= 600
    if looks_like_spaced_letters(title):
        score -= 2_500
    return score


def _score_ocr_lines(lines: list[str], *, student_name: str) -> int:
    if not lines or looks_like_revision_document_page(lines):
        return -1
    title, confidence, method = title_from_cover_lines_with_confidence(lines, student_name)
    return _title_quality_score(title, confidence, method=method, source="ocr")


def _best_ocr_on_crop(
    image,
    *,
    student_name: str,
    top_fraction: float,
) -> _OcrCandidate | None:
    cropped = crop_title_region(image, top_fraction=top_fraction)
    best: _OcrCandidate | None = None

    for psm in OCR_PSM_MODES:
        try:
            lines = ocr_image_to_lines(cropped, psm=psm)
        except Exception as exc:
            LOGGER.debug("OCR psm=%s failed: %s", psm, exc)
            continue
        if not lines:
            continue
        score = _score_ocr_lines(lines, student_name=student_name)
        if best is None or score > best.score:
            best = _OcrCandidate(
                lines=lines,
                score=score,
                psm=psm,
                top_fraction=top_fraction,
            )

    return best


def ocr_cover_lines(path: Path, *, student_name: str = "") -> list[str]:
    """Multi-pass OCR with preprocessing; returns the best line set for title parsing."""
    ensure_tesseract_available()

    image = render_cover_page_image(path)
    if image is None:
        return []

    prepared = preprocess_for_ocr(image)
    best = _best_ocr_on_crop(
        prepared,
        student_name=student_name,
        top_fraction=OCR_TOP_FRACTION,
    )

    if best is None or best.score < OCR_RETRY_SCORE:
        alternate = _best_ocr_on_crop(
            prepared,
            student_name=student_name,
            top_fraction=OCR_TOP_FRACTION_ALT,
        )
        if alternate is not None and (best is None or alternate.score > best.score):
            best = alternate

    if best is None:
        return []

    if best.score >= 0:
        LOGGER.debug(
            "OCR selected psm=%s top=%.0f%% score=%s for %s",
            best.psm,
            best.top_fraction * 100,
            best.score,
            path.name,
        )
    return best.lines


def _best_cover_source(
    pdf_lines: list[str],
    ocr_lines: list[str],
    *,
    student_name: str,
) -> CoverPageLines:
    candidates: list[tuple[list[str], str, int]] = []

    for lines, source in ((pdf_lines, "pdf_text"), (ocr_lines, "ocr")):
        if not lines or looks_like_revision_document_page(lines):
            continue
        title, confidence, method = title_from_cover_lines_with_confidence(lines, student_name)
        score = _title_quality_score(title, confidence, method=method, source=source)
        if score >= 0:
            candidates.append((lines, source, score))

    if not candidates:
        if ocr_lines:
            return CoverPageLines(tuple(ocr_lines), "ocr")
        if pdf_lines:
            return CoverPageLines(tuple(pdf_lines), "pdf_text")
        return CoverPageLines(tuple(), "empty")

    candidates.sort(key=lambda item: item[2], reverse=True)
    best_lines, best_source, _ = candidates[0]
    return CoverPageLines(tuple(best_lines), best_source)


def _pdf_text_is_sufficient(lines: list[str], *, student_name: str) -> bool:
    """Embedded PDF text is trusted when layout parsing finds a plausible title."""
    if not lines or looks_like_revision_document_page(lines):
        return False
    if not student_name:
        return len(lines) >= 3
    title, confidence, method = title_from_cover_lines_with_confidence(lines, student_name)
    if not title:
        return False
    if confidence == "High":
        return True
    return confidence == "Medium" and method in STRONG_LAYOUT_METHODS


@lru_cache(maxsize=512)
def _cached_cover_page_lines(
    resolved_path: str,
    mtime_ns: int,
    size: int,
    student_name: str,
    pipeline_version: int,
) -> CoverPageLines:
    del mtime_ns, size, pipeline_version
    path = Path(resolved_path)

    pdf_lines = pdf_text_cover_lines(path)
    if _pdf_text_is_sufficient(pdf_lines, student_name=student_name):
        return CoverPageLines(tuple(pdf_lines), "pdf_text")

    try:
        ocr_lines = ocr_cover_lines(path, student_name=student_name)
    except Exception as exc:
        LOGGER.warning("OCR failed for %s: %s", path.name, exc)
        ocr_lines = []

    return _best_cover_source(pdf_lines, ocr_lines, student_name=student_name)


def read_cover_page_lines(path: Path, *, student_name: str = "") -> CoverPageLines:
    """Read cover lines once per PDF, preferring embedded text when it is enough."""
    stat = path.stat()
    return _cached_cover_page_lines(
        str(path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        student_name,
        OCR_PIPELINE_VERSION,
    )


def ocr_cover_lines_legacy(path: Path) -> list[str]:
    """Backward-compatible helper returning only OCR lines."""
    return list(read_cover_page_lines(path).lines)


def extract_title_from_ocr(path: Path, *, student_name: str) -> str:
    from thesis2lib.cover_layout import title_from_cover_lines

    cover = read_cover_page_lines(path, student_name=student_name)
    return title_from_cover_lines(list(cover.lines), student_name)
