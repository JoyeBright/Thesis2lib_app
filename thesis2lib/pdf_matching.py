"""Local PDF matching and revision classification."""

from __future__ import annotations

import re
from pathlib import Path

from thesis2lib.constants import (
    AMBIGUOUS_REVISION_TERMS,
    CANVAS_BULK_PREFIX,
    STRONG_REVISION_TERMS,
)
from thesis2lib.models import PdfSelection
from thesis2lib.pdf_content import revision_document_filename
from thesis2lib.utils import clean_text, normalize_identifier


def normalize_filename(name: str) -> str:
    text = Path(name).name.casefold()
    if text.endswith(".pdf"):
        text = text[:-4]
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = CANVAS_BULK_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_filename_for_classification(filename: str) -> str:
    return re.sub(r"\s+", " ", normalize_filename(filename)).strip()


def is_strong_revision_document(path: Path) -> bool:
    normalized = normalize_filename_for_classification(path.stem)
    return any(term in normalized for term in STRONG_REVISION_TERMS)


def has_ambiguous_revision_wording(path: Path) -> bool:
    if is_strong_revision_document(path):
        return False
    normalized = normalize_filename_for_classification(path.stem)
    return any(term in normalized for term in AMBIGUOUS_REVISION_TERMS)


def multiple_thesis_selection_status(candidates: list[Path]) -> str:
    normalized_names = [
        normalize_filename_for_classification(p.stem) for p in candidates
    ]
    if any(has_ambiguous_revision_wording(p) for p in candidates):
        return "Manual Review"
    if sum("thesis" in name for name in normalized_names) > 1:
        return "Manual Review"
    return "Multiple PDFs"


def token_present(filename: str, identifier: str) -> bool:
    identifier = normalize_identifier(identifier)
    if not identifier:
        return False
    pattern = rf"(?<!\d){re.escape(identifier)}(?!\d)"
    return re.search(pattern, filename) is not None


def canvas_id_in_filename(filename: str, canvas_id: str) -> bool:
    """Match Canvas bulk-download names: login_<canvas_id>_<submission_id>_...."""
    canvas_id = normalize_identifier(canvas_id)
    if not canvas_id:
        return False
    if token_present(filename, canvas_id):
        return True
    pattern = rf"(?:^|[_.-]){re.escape(canvas_id)}(?:[_.-]|\.pdf$)"
    return re.search(pattern, filename, flags=re.IGNORECASE) is not None


def attachment_names_from_submission(submission: dict | None) -> list[str]:
    if not submission:
        return []
    names: list[str] = []
    for attachment in submission.get("attachments") or []:
        for key in ("display_name", "filename"):
            value = clean_text(attachment.get(key))
            if value:
                names.append(value)
    return list(dict.fromkeys(names))


def match_pdf_to_student(
    pdf: Path,
    sis_user_id: str,
    canvas_id: str,
    canvas_attachment_names: list[str],
) -> tuple[bool, str, str]:
    normalized_local = normalize_filename(pdf.name)
    for attachment_name in canvas_attachment_names:
        if normalized_local == normalize_filename(attachment_name):
            return True, "Canvas attachment filename", "High"

    if canvas_id and canvas_id_in_filename(pdf.name, canvas_id):
        return True, "Canvas ID", "High"

    return False, "", ""


def pick_best_thesis_candidate(
    candidates: list[Path],
    canvas_attachment_names: list[str],
) -> Path | None:
    if not candidates:
        return None

    for pdf in candidates:
        normalized_local = normalize_filename(pdf.name)
        for attachment_name in canvas_attachment_names:
            if normalized_local == normalize_filename(attachment_name):
                return pdf

    with_thesis = [
        pdf
        for pdf in candidates
        if "thesis" in normalize_filename_for_classification(pdf.stem)
    ]
    if len(with_thesis) == 1:
        return with_thesis[0]

    return None


def pick_latest_path(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    existing = [path for path in candidates if path.is_file()]
    pool = existing or candidates
    return max(pool, key=lambda path: path.stat().st_mtime if path.is_file() else 0)


def find_matched_pdfs(
    pdfs: list[Path],
    sis_user_id: str,
    canvas_id: str,
    canvas_attachment_names: list[str],
) -> list[Path]:
    matched: list[Path] = []
    for pdf in pdfs:
        is_match, _, _ = match_pdf_to_student(
            pdf, sis_user_id, canvas_id, canvas_attachment_names
        )
        if is_match:
            matched.append(pdf)
    return matched


def select_pdf_candidate(
    pdfs: list[Path],
    sis_user_id: str,
    canvas_id: str,
    canvas_attachment_names: list[str],
) -> PdfSelection:
    matched: list[tuple[Path, str, str]] = []
    for pdf in pdfs:
        is_match, method, confidence = match_pdf_to_student(
            pdf, sis_user_id, canvas_id, canvas_attachment_names
        )
        if is_match:
            matched.append((pdf, method, confidence))

    if not matched:
        return PdfSelection(
            status="Missing PDF",
            selected_path=None,
            candidates=[],
            revision_documents=[],
            matching_method="None",
            confidence="None",
            detail="No local PDF matched the student's Canvas ID or Canvas attachment names.",
            canvas_attachment_names=canvas_attachment_names,
        )

    candidates = [item[0] for item in matched]
    methods = {item[1] for item in matched if item[1]}
    confidences = {item[2] for item in matched if item[2]}
    matching_method = "; ".join(sorted(methods)) or "Unknown"
    confidence = (
        "High"
        if "High" in confidences
        else ("Medium" if "Medium" in confidences else "Low")
    )

    if len(candidates) == 1:
        selected = candidates[0]
        return PdfSelection(
            status="Matched",
            selected_path=selected,
            candidates=candidates,
            revision_documents=[],
            matching_method=matching_method,
            confidence="High",
            detail="Submission contains a single PDF; treated as the final thesis.",
            canvas_attachment_names=canvas_attachment_names,
            needs_review=False,
            single_pdf_final_submission=True,
        )

    revision_documents = [p for p in candidates if is_strong_revision_document(p)]
    if len(revision_documents) > 1:
        latest_revision = pick_latest_path(revision_documents)
        revision_documents = [latest_revision] if latest_revision else revision_documents
    thesis_candidates = [p for p in candidates if not is_strong_revision_document(p)]

    if len(thesis_candidates) == 1:
        selected = thesis_candidates[0]
        detail = "Exactly one clear thesis candidate after revision filtering."
        if revision_documents:
            detail += (
                " Strong revision document(s) recorded separately: "
                + "; ".join(p.name for p in revision_documents)
                + "."
            )
        needs_review = confidence != "High" or revision_document_filename(selected.name)
        return PdfSelection(
            status="Matched",
            selected_path=selected,
            candidates=candidates,
            revision_documents=revision_documents,
            matching_method=matching_method,
            confidence=confidence,
            detail=detail,
            canvas_attachment_names=canvas_attachment_names,
            needs_review=needs_review,
        )

    if not thesis_candidates and revision_documents:
        return PdfSelection(
            status="Manual Review",
            selected_path=None,
            candidates=candidates,
            revision_documents=revision_documents,
            matching_method=matching_method,
            confidence=confidence,
            detail=(
                "Only strong revision/change document(s) matched; "
                "no clear thesis PDF remains."
            ),
            canvas_attachment_names=canvas_attachment_names,
            needs_review=True,
        )

    if len(thesis_candidates) > 1:
        latest = pick_latest_path(thesis_candidates)
        if latest is not None:
            detail = "Multiple thesis files on one submission; selected the latest file."
            if revision_documents:
                detail += (
                    " Revision document(s) recorded separately: "
                    + "; ".join(p.name for p in revision_documents)
                    + "."
                )
            return PdfSelection(
                status="Matched",
                selected_path=latest,
                candidates=candidates,
                revision_documents=revision_documents,
                matching_method=matching_method,
                confidence="High",
                detail=detail,
                canvas_attachment_names=canvas_attachment_names,
                needs_review=False,
            )

        status = multiple_thesis_selection_status(thesis_candidates)
        return PdfSelection(
            status=status,
            selected_path=None,
            candidates=candidates,
            revision_documents=revision_documents,
            matching_method=matching_method,
            confidence=confidence,
            detail=(
                "Multiple plausible thesis PDFs matched: "
                + "; ".join(p.name for p in thesis_candidates)
            ),
            canvas_attachment_names=canvas_attachment_names,
            needs_review=True,
        )

    return PdfSelection(
        status="Manual Review",
        selected_path=None,
        candidates=candidates,
        revision_documents=revision_documents,
        matching_method=matching_method,
        confidence=confidence,
        detail="PDF candidates could not be classified confidently.",
        canvas_attachment_names=canvas_attachment_names,
        needs_review=True,
    )


def select_library_pdf_candidate(
    first_pdf_pool: list[Path] | None,
    second_pdf_pool: list[Path] | None,
    first_raw: dict | None,
    second_raw: dict | None,
    final_attempt: str,
    sis_user_id: str,
    canvas_id: str,
) -> PdfSelection:
    if not final_attempt:
        return PdfSelection(
            status="Missing PDF",
            selected_path=None,
            candidates=[],
            revision_documents=[],
            matching_method="None",
            confidence="None",
            detail="No graded submission PDF matched the student's files.",
        )

    thesis_raw = second_raw if final_attempt == "Second" else first_raw
    thesis_pool = second_pdf_pool if final_attempt == "Second" else first_pdf_pool
    attachment_names = attachment_names_from_submission(thesis_raw)
    thesis_selection = select_pdf_candidate(
        thesis_pool or [],
        sis_user_id,
        canvas_id,
        attachment_names,
    )

    first_names = attachment_names_from_submission(first_raw)
    second_names = attachment_names_from_submission(second_raw)
    combined_names = list(dict.fromkeys(first_names + second_names))
    combined_candidates: list[Path] = []
    if first_pdf_pool is not None:
        combined_candidates.extend(
            find_matched_pdfs(
                first_pdf_pool,
                sis_user_id,
                canvas_id,
                first_names,
            )
        )
    if second_pdf_pool is not None:
        combined_candidates.extend(
            find_matched_pdfs(
                second_pdf_pool,
                sis_user_id,
                canvas_id,
                second_names,
            )
        )
    combined_candidates = list(dict.fromkeys(combined_candidates))
    thesis_selection.candidates = combined_candidates
    thesis_selection.canvas_attachment_names = combined_names

    final_raw = second_raw if final_attempt == "Second" else first_raw
    final_pool = second_pdf_pool if final_attempt == "Second" else first_pdf_pool
    final_names = attachment_names_from_submission(final_raw)
    if final_pool is not None:
        final_candidates = find_matched_pdfs(
            final_pool,
            sis_user_id,
            canvas_id,
            final_names,
        )
        thesis_selection.revision_documents = [
            path for path in final_candidates if is_strong_revision_document(path)
        ]
        if len(thesis_selection.revision_documents) > 1:
            latest_revision = pick_latest_path(thesis_selection.revision_documents)
            thesis_selection.revision_documents = (
                [latest_revision] if latest_revision else thesis_selection.revision_documents
            )

    if (
        final_raw is not None
        and final_pool is not None
    ):
        final_names = attachment_names_from_submission(final_raw)
        final_matched = find_matched_pdfs(
            final_pool,
            sis_user_id,
            canvas_id,
            final_names,
        )
        if len(final_matched) == 1:
            only_pdf = final_matched[0]
            thesis_selection.status = "Matched"
            thesis_selection.selected_path = only_pdf
            thesis_selection.revision_documents = []
            thesis_selection.confidence = "High"
            thesis_selection.needs_review = False
            thesis_selection.single_pdf_final_submission = True
            thesis_selection.single_pdf_second_submission = final_attempt == "Second"
            thesis_selection.detail = (
                f"{final_attempt} submission contains a single PDF; treated as the final thesis. "
                "No separate revision document was submitted."
            )

    return thesis_selection
