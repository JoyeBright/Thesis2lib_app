"""Select and download thesis PDFs from Canvas submission attachments."""

from __future__ import annotations

from pathlib import Path

from thesis2lib.models import PdfSelection
from thesis2lib.pdf_content import revision_document_filename
from thesis2lib.pdf_matching import (
    attachment_names_from_submission,
    is_strong_revision_document,
    multiple_thesis_selection_status,
    pick_best_thesis_candidate,
)
from thesis2lib.utils import clean_text


def attachment_label(attachment: dict) -> str:
    for key in ("display_name", "filename"):
        value = clean_text(attachment.get(key))
        if value:
            return value
    attachment_id = clean_text(attachment.get("id"))
    return f"attachment_{attachment_id or 'unknown'}.pdf"


def attachment_path_label(attachment: dict) -> Path:
    label = attachment_label(attachment)
    if not label.lower().endswith(".pdf"):
        label = f"{label}.pdf"
    return Path(label)


def is_pdf_attachment(attachment: dict) -> bool:
    label = attachment_label(attachment).lower()
    if label.endswith(".pdf"):
        return True
    content_type = clean_text(attachment.get("content-type")).lower()
    return content_type == "application/pdf"


def attachment_download_url(attachment: dict) -> str:
    return clean_text(attachment.get("url"))


def attachment_timestamp(attachment: dict) -> str:
    for key in ("created_at", "updated_at"):
        value = clean_text(attachment.get(key))
        if value:
            return value
    return ""


def sort_attachments_latest_first(attachments: list[dict]) -> list[dict]:
    return sorted(
        attachments,
        key=lambda item: (attachment_timestamp(item), str(item.get("id") or "")),
        reverse=True,
    )


def pick_latest_attachment(attachments: list[dict]) -> dict | None:
    ordered = sort_attachments_latest_first(attachments)
    return ordered[0] if ordered else None


def thesis_pdf_selected(selection: PdfSelection | None) -> bool:
    if selection is None:
        return False
    return selection.selected_path is not None or selection.selected_attachment is not None


def _dedupe_pdf_attachments(attachments: list[dict]) -> list[dict]:
    seen: set[str | int] = set()
    deduped: list[dict] = []
    for attachment in attachments:
        attachment_id = attachment.get("id")
        if attachment_id in seen:
            continue
        if not is_pdf_attachment(attachment):
            continue
        seen.add(attachment_id)
        deduped.append(attachment)
    return deduped


def pdf_attachments_from_submission_raw(raw: dict | None) -> list[dict]:
    if not raw:
        return []
    attachments: list[dict] = []
    attachments.extend(raw.get("attachments") or [])
    for entry in raw.get("submission_history") or []:
        if isinstance(entry, dict):
            attachments.extend(entry.get("attachments") or [])
    return _dedupe_pdf_attachments(attachments)


def current_pdf_attachments_from_submission_raw(raw: dict | None) -> list[dict]:
    """PDFs on the submission as graded — current attachments only, not resubmit history."""
    if not raw:
        return []
    return _dedupe_pdf_attachments(list(raw.get("attachments") or []))


def submission_has_single_pdf(raw: dict | None) -> bool:
    """True when the graded submission contains exactly one PDF."""
    return len(current_pdf_attachments_from_submission_raw(raw)) == 1


def second_submission_has_single_pdf(second_raw: dict | None) -> bool:
    """True when the 2nd Canvas submission slot contains exactly one PDF."""
    return submission_has_single_pdf(second_raw)


def final_submission_raw(
    final_attempt: str | None,
    first_raw: dict | None,
    second_raw: dict | None,
) -> dict | None:
    if final_attempt == "Second":
        return second_raw
    if final_attempt == "First":
        return first_raw
    return None


def apply_single_pdf_final_submission_rules(
    selection: PdfSelection,
    *,
    final_attempt: str,
    first_raw: dict | None,
    second_raw: dict | None,
) -> PdfSelection:
    """One PDF on the graded final submission is treated as the thesis; no revision file."""
    final_raw = final_submission_raw(final_attempt, first_raw, second_raw)
    if not submission_has_single_pdf(final_raw):
        return selection

    attachment = current_pdf_attachments_from_submission_raw(final_raw)[0]
    label = attachment_path_label(attachment)
    selection.status = "Matched"
    selection.selected_attachment = attachment
    selection.selected_path = None
    selection.revision_documents = []
    selection.confidence = "High"
    selection.needs_review = False
    selection.single_pdf_final_submission = True
    selection.single_pdf_second_submission = final_attempt == "Second"
    selection.matching_method = selection.matching_method or "Canvas submission attachment"
    selection.detail = (
        f"{final_attempt} submission contains a single PDF; treated as the final thesis. "
        "No separate revision document was submitted."
    )
    if attachment not in (selection.candidate_attachments or []):
        merged_attachments = list(selection.candidate_attachments or [])
        merged_attachments.append(attachment)
        selection.candidate_attachments = _dedupe_pdf_attachments(merged_attachments)
    if label not in selection.candidates:
        selection.candidates = list(dict.fromkeys([*selection.candidates, label]))
    return selection


def apply_second_submission_single_pdf_rules(
    selection: PdfSelection,
    *,
    final_attempt: str,
    second_raw: dict | None,
) -> PdfSelection:
    """Backward-compatible wrapper around apply_single_pdf_final_submission_rules."""
    return apply_single_pdf_final_submission_rules(
        selection,
        final_attempt=final_attempt,
        first_raw=None,
        second_raw=second_raw,
    )


def revision_documents_from_attachments(pdf_attachments: list[dict]) -> list[Path]:
    revision_items = [
        attachment
        for attachment in pdf_attachments
        if is_strong_revision_document(attachment_path_label(attachment))
    ]
    if len(revision_items) > 1:
        latest = pick_latest_attachment(revision_items)
        return [attachment_path_label(latest)] if latest else []
    return [attachment_path_label(item) for item in revision_items]


def _merge_library_selection(
    thesis_selection: PdfSelection,
    *,
    thesis_attempt: str,
    final_attempt: str,
    first_raw: dict | None,
    second_raw: dict | None,
) -> PdfSelection:
    all_attachments = _dedupe_pdf_attachments(
        current_pdf_attachments_from_submission_raw(first_raw)
        + current_pdf_attachments_from_submission_raw(second_raw)
    )
    thesis_selection.candidate_attachments = all_attachments
    thesis_selection.candidates = [attachment_path_label(item) for item in all_attachments]

    final_raw = final_submission_raw(final_attempt, first_raw, second_raw)
    if not submission_has_single_pdf(final_raw):
        revision_documents = revision_documents_from_attachments(
            current_pdf_attachments_from_submission_raw(final_raw)
        )
        if revision_documents:
            thesis_selection.revision_documents = revision_documents
            if thesis_selection.detail and "revision document" not in thesis_selection.detail.lower():
                thesis_selection.detail += (
                    " Revision document(s) on final submission: "
                    + "; ".join(path.name for path in revision_documents)
                    + "."
                )

    if thesis_attempt != final_attempt and thesis_selection.status == "Matched":
        thesis_selection.detail = (
            f"Library thesis taken from {thesis_attempt} submission. "
            + thesis_selection.detail
        )
    return apply_single_pdf_final_submission_rules(
        thesis_selection,
        final_attempt=final_attempt,
        first_raw=first_raw,
        second_raw=second_raw,
    )


def select_library_canvas_attachments(
    first_raw: dict | None,
    second_raw: dict | None,
    final_attempt: str,
) -> PdfSelection:
    if not final_attempt:
        return PdfSelection(
            status="Missing PDF",
            selected_path=None,
            candidates=[],
            revision_documents=[],
            matching_method="None",
            confidence="None",
            detail="No graded Canvas submission was found for library delivery.",
            pdf_source="canvas",
        )

    thesis_raw = second_raw if final_attempt == "Second" else first_raw
    attachments = (thesis_raw or {}).get("attachments") or []
    thesis_selection = select_canvas_attachments(
        attachments if isinstance(attachments, list) else []
    )
    return _merge_library_selection(
        thesis_selection,
        thesis_attempt=final_attempt,
        final_attempt=final_attempt,
        first_raw=first_raw,
        second_raw=second_raw,
    )


def select_canvas_attachments(attachments: list[dict]) -> PdfSelection:
    pdf_attachments = [item for item in attachments if is_pdf_attachment(item)]
    attachment_names = [
        name
        for name in attachment_names_from_submission({"attachments": pdf_attachments})
        if name
    ]

    if not pdf_attachments:
        return PdfSelection(
            status="Missing PDF",
            selected_path=None,
            candidates=[],
            revision_documents=[],
            matching_method="None",
            confidence="None",
            detail="No PDF attachments were found on the Canvas submission.",
            canvas_attachment_names=attachment_names,
            pdf_source="canvas",
        )

    candidate_paths = [attachment_path_label(item) for item in pdf_attachments]
    revision_attachment_items = [
        item
        for item, path in zip(pdf_attachments, candidate_paths, strict=True)
        if is_strong_revision_document(path)
    ]
    revision_documents = [
        attachment_path_label(item)
        for item in (
            [pick_latest_attachment(revision_attachment_items)]
            if len(revision_attachment_items) > 1
            else revision_attachment_items
        )
        if item is not None
    ]
    thesis_attachments = [
        item
        for item, path in zip(pdf_attachments, candidate_paths, strict=True)
        if not is_strong_revision_document(path)
    ]
    thesis_paths = [attachment_path_label(item) for item in thesis_attachments]

    def build_selection(
        *,
        status: str,
        selected_attachment: dict | None,
        selected_path: Path | None,
        detail: str,
        confidence: str = "High",
    ) -> PdfSelection:
        revision_paths = revision_documents
        detail_text = detail
        if revision_paths:
            detail_text += (
                " Strong revision document(s) recorded separately: "
                + "; ".join(path.name for path in revision_paths)
                + "."
            )
        return PdfSelection(
            status=status,
            selected_attachment=selected_attachment,
            selected_path=selected_path,
            candidates=candidate_paths,
            revision_documents=revision_paths,
            matching_method="Canvas submission attachment",
            confidence=confidence,
            detail=detail_text,
            canvas_attachment_names=attachment_names,
            pdf_source="canvas",
            candidate_attachments=list(pdf_attachments),
            needs_review=confidence in {"Low", "None"} or status != "Matched",
        )

    if len(pdf_attachments) == 1:
        attachment = pdf_attachments[0]
        return build_selection(
            status="Matched",
            selected_attachment=attachment,
            selected_path=None,
            detail="Submission contains a single PDF; treated as the final thesis.",
            confidence="High",
        )

    if len(thesis_attachments) == 1:
        attachment = thesis_attachments[0]
        label = attachment_label(attachment)
        needs_review = revision_document_filename(label)
        return build_selection(
            status="Matched",
            selected_attachment=attachment,
            selected_path=None,
            detail="Exactly one clear thesis attachment after revision filtering.",
            confidence="Medium" if needs_review else "High",
        )

    if not thesis_attachments and revision_documents:
        return build_selection(
            status="Manual Review",
            selected_attachment=None,
            selected_path=None,
            detail=(
                "Only strong revision/change attachment(s) were found; "
                "no clear thesis PDF remains."
            ),
        )

    if len(thesis_attachments) > 1:
        def attachment_priority(attachment: dict) -> tuple[int, str]:
            label = attachment_label(attachment).lower()
            score = 0
            if any(
                term in label
                for term in (
                    "feedback",
                    "response",
                    "comment",
                    "revision document",
                    "response letter",
                )
            ):
                score -= 40
            if "thesis" in label and "feedback" not in label:
                score += 25
            if any(term in label for term in ("final", "master", "correct")):
                score += 10
            return score, attachment_timestamp(attachment)

        chosen = max(thesis_attachments, key=attachment_priority)
        return build_selection(
            status="Matched",
            selected_attachment=chosen,
            selected_path=None,
            detail=(
                "Multiple thesis files on one submission; selected the best thesis "
                f"attachment ({attachment_label(chosen)})."
            ),
            confidence="High",
        )

    return build_selection(
        status="Manual Review",
        selected_attachment=None,
        selected_path=None,
        detail="Canvas attachments could not be classified confidently.",
        confidence="Low",
    )
