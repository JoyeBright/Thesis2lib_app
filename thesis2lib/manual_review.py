"""Interactive manual review for uncertain PDF and title selections."""

from __future__ import annotations

import logging
import select
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from thesis2lib.canvas_attachments import (
    attachment_label,
    attachment_path_label,
    current_pdf_attachments_from_submission_raw,
    submission_has_single_pdf,
)
from thesis2lib.models import OverviewWorkbook, PdfSelection, ProcessedStudent
from thesis2lib.package_writer import delivery_package_is_eligible
from thesis2lib.pdf_content import revision_document_filename
from thesis2lib.pipeline import determine_package_status, refresh_student_overview_data
from thesis2lib.progress import ConsoleProgress
from thesis2lib.overview_reader import overview_has_snr
from thesis2lib.text_polish import normalize_manual_thesis_title
from thesis2lib.thesis_title import (
    extract_thesis_title_result,
    extracted_title_differs_from_overview,
    extracted_title_looks_unreliable,
)
from thesis2lib.utils import clean_text, normalize_identifier

LOGGER = logging.getLogger(__name__)

DEFAULT_DECISIONS_FILENAME = "manual_decisions.yml"
MANUAL_DECISIONS_FOLDER = "Manual"
LEGACY_DECISIONS_SUBDIR = ".thesis2lib_cache"
MANUAL_DECISIONS_VERSION = 2
CATALOG_HEADER = (
    "# Thesis2Lib manual decisions.\n"
    "# Reference fields: extracted_title, selected_pdf, detected_revision (auto-detected).\n"
    "# Edit thesis_title, thesis_pdf, or revision_pdf below; set *_confirmed: true after checking.\n"
    "# canvas_url opens the final submission in Canvas SpeedGrader.\n"
    "# Optional: overview_snr, thesis_pdf, revision_pdf.\n"
)


@dataclass(frozen=True)
class CanvasReviewContext:
    canvas_url: str
    course_id: int
    first_assignment_id: int
    second_assignment_id: int
    open_browser: bool = True


def canvas_speedgrader_url(
    canvas_url: str,
    course_id: int,
    assignment_id: int,
    student_canvas_id: str,
) -> str:
    parsed = urlparse(canvas_url.strip())
    base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return (
        f"{base}/courses/{course_id}/gradebook/speed_grader"
        f"?assignment_id={assignment_id}&student_id={student_canvas_id}"
    )


def assignment_id_for_final_attempt(
    final_attempt: str | None,
    *,
    first_assignment_id: int,
    second_assignment_id: int,
) -> int | None:
    if final_attempt == "Second":
        return second_assignment_id
    if final_attempt == "First":
        return first_assignment_id
    return None


def show_canvas_submission(item: ProcessedStudent, context: CanvasReviewContext | None) -> None:
    if context is None or not item.final_attempt:
        return
    assignment_id = assignment_id_for_final_attempt(
        item.final_attempt,
        first_assignment_id=context.first_assignment_id,
        second_assignment_id=context.second_assignment_id,
    )
    if assignment_id is None:
        return
    url = canvas_speedgrader_url(
        context.canvas_url,
        context.course_id,
        assignment_id,
        item.student.canvas_id,
    )
    print(f"Canvas: {url}")
    if context.open_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception as exc:
            LOGGER.warning("Could not open browser for %s: %s", item.student.name, exc)


@dataclass
class StudentDecision:
    thesis_pdf: str = ""
    revision_pdf: str = ""
    thesis_title: str = ""
    overview_snr: str = ""


@dataclass(frozen=True)
class ManualCheckFlags:
    """Independent manual-review dimensions for one student."""

    snr: bool = False
    thesis_pdf: bool = False
    revision: bool = False
    title: bool = False

    @property
    def any(self) -> bool:
        return self.snr or self.thesis_pdf or self.revision or self.title

    def labels(self) -> list[str]:
        labels: list[str] = []
        if self.snr:
            labels.append("Overview SNR")
        if self.thesis_pdf:
            labels.append("Thesis PDF")
        if self.revision:
            labels.append("Revision PDF")
        if self.title:
            labels.append("Thesis title")
        return labels


def manual_decisions_folder(output_folder: Path) -> Path:
    return output_folder / MANUAL_DECISIONS_FOLDER


def legacy_manual_decisions_path(output_folder: Path) -> Path:
    return output_folder / LEGACY_DECISIONS_SUBDIR / DEFAULT_DECISIONS_FILENAME


def manual_decisions_path(output_folder: Path, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    return manual_decisions_folder(output_folder) / DEFAULT_DECISIONS_FILENAME


def load_manual_decision_store(
    output_folder: Path,
    override: Path | None = None,
) -> ManualDecisionStore:
    path = manual_decisions_path(output_folder, override)
    if override is None:
        legacy = legacy_manual_decisions_path(output_folder)
        if not path.is_file() and legacy.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
            LOGGER.info(
                "Loaded saved manual decisions from legacy cache into %s "
                "(used as prompt defaults until you confirm each one).",
                path,
            )
    return ManualDecisionStore.load(path)


class ManualDecisionStore:
    def __init__(self, path: Path):
        self.path = path
        self.students: dict[str, dict[str, str]] = {}

    @classmethod
    def load(cls, path: Path) -> ManualDecisionStore:
        store = cls(path)
        if not path.is_file():
            return store
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        students = raw.get("students") or {}
        if isinstance(students, dict):
            store.students = {
                str(key): {
                    str(field): str(value)
                    for field, value in (record or {}).items()
                    if value not in (None, "")
                }
                for key, record in students.items()
            }
        return store

    def get(self, canvas_id: str) -> StudentDecision:
        record = self.students.get(str(canvas_id), {})
        return StudentDecision(
            thesis_pdf=record.get("thesis_pdf", ""),
            revision_pdf=record.get("revision_pdf", ""),
            thesis_title=record.get("thesis_title", ""),
            overview_snr=record.get("overview_snr", ""),
        )

    def save(self, canvas_id: str, decision: StudentDecision) -> None:
        key = str(canvas_id)
        record = dict(self.students.get(key, {}))
        for field, value in {
            "thesis_pdf": decision.thesis_pdf,
            "revision_pdf": decision.revision_pdf,
            "thesis_title": decision.thesis_title,
            "overview_snr": decision.overview_snr,
        }.items():
            if value:
                record[field] = value
        if record:
            self.students[key] = record
        else:
            self.students.pop(key, None)

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sorted_students = dict(
            sorted(
                self.students.items(),
                key=lambda item: (
                    str(item[1].get("student_name", "")).casefold(),
                    item[0],
                ),
            )
        )
        payload = {"version": MANUAL_DECISIONS_VERSION, "students": sorted_students}
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        self.path.write_text(f"{CATALOG_HEADER}{text}", encoding="utf-8")
        legacy = self.path.parent.parent / LEGACY_DECISIONS_SUBDIR / DEFAULT_DECISIONS_FILENAME
        if legacy.parent.exists() or self.path.parent.parent.exists():
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(text, encoding="utf-8")


def _is_confirmed(record: dict[str, str], field: str) -> bool:
    return clean_text(record.get(field, "")).lower() in {"true", "yes", "1"}


def pdf_selection_needs_review(item: ProcessedStudent) -> bool:
    if item.student.course_result != "Passed" or not item.final_attempt:
        return False
    pdf = item.pdf_selection
    if pdf is None:
        return True
    if pdf.confidence == "High" and pdf.status == "Matched":
        return False
    if pdf.needs_review:
        return True
    if pdf.status in {"Manual Review", "Multiple PDFs"}:
        return True
    if pdf.confidence in {"Low", "None"}:
        return True
    if pdf.status != "Matched":
        return True
    return False


def overview_snr_needs_review(
    item: ProcessedStudent,
    *,
    overview_workbook_provided: bool,
) -> bool:
    """True when the overview workbook row still needs the correct SNR link."""
    if not overview_workbook_provided:
        return False
    if item.student.course_result != "Passed" or not item.final_attempt:
        return False
    archive = item.archive_metadata
    if archive is None or not archive.found:
        return True
    linked_snr = normalize_identifier(item.overview_snr or archive.sis_user_id)
    return not linked_snr


def _second_submission_raw(item: ProcessedStudent) -> dict | None:
    second = item.second_submission
    return second.raw if second else None


def _final_submission_raw(item: ProcessedStudent) -> dict | None:
    if item.final_attempt == "Second":
        second = item.second_submission
        return second.raw if second else None
    if item.final_attempt == "First":
        first = item.first_submission
        return first.raw if first else None
    return None


def _second_submission_has_single_pdf(item: ProcessedStudent) -> bool:
    pdf = item.pdf_selection
    if pdf and pdf.single_pdf_second_submission:
        return True
    return submission_has_single_pdf(_second_submission_raw(item))


def _final_submission_has_single_pdf(item: ProcessedStudent) -> bool:
    pdf = item.pdf_selection
    if pdf and pdf.single_pdf_final_submission:
        return True
    return submission_has_single_pdf(_final_submission_raw(item))


def _final_submission_pdf_count(item: ProcessedStudent) -> int:
    return len(current_pdf_attachments_from_submission_raw(_final_submission_raw(item)))


def _second_submission_pdf_count(item: ProcessedStudent) -> int:
    return len(current_pdf_attachments_from_submission_raw(_second_submission_raw(item)))


def thesis_pdf_needs_review(
    item: ProcessedStudent,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    """Ask the user to confirm the final thesis PDF before delivery."""
    if not delivery_package_is_eligible(item):
        return False
    record = catalog_record or {}
    if _is_confirmed(record, "thesis_pdf_confirmed"):
        return False
    pdf = item.pdf_selection
    if pdf and pdf.confidence == "Manual":
        return False
    if _final_submission_has_single_pdf(item):
        return False
    if (
        pdf
        and pdf.status == "Matched"
        and pdf.confidence == "High"
        and not pdf.needs_review
    ):
        return False
    if pdf_selection_needs_review(item):
        return True
    return False


def revision_needs_review(
    item: ProcessedStudent,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    """Ask which 2nd-submission file is the revision document, if any."""
    if not delivery_package_is_eligible(item):
        return False
    if item.final_attempt != "Second":
        return False
    if _second_submission_has_single_pdf(item):
        return False
    record = catalog_record or {}
    if _is_confirmed(record, "revision_confirmed"):
        return False
    return _second_submission_pdf_count(item) > 1


def title_needs_review(item: ProcessedStudent) -> bool:
    if not delivery_package_is_eligible(item):
        return False
    if item.thesis_title_confidence in {"High", "Manual"}:
        return False
    if item.thesis_title_needs_review:
        return True
    if extracted_title_looks_unreliable(
        item.thesis_title or "",
        item.thesis_title_confidence or "None",
    ):
        return True
    archive = item.archive_metadata
    if archive and archive.found and archive.thesis_title:
        if extracted_title_differs_from_overview(
            item.thesis_title or "",
            archive.thesis_title,
        ):
            return True
    return False


def thesis_pdf_needs_confirmation(
    item: ProcessedStudent,
    decision: StudentDecision,
) -> bool:
    """Deprecated alias — use thesis_pdf_needs_review instead."""
    del decision
    return thesis_pdf_needs_review(item)


def saved_title_needs_confirmation(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    if not delivery_package_is_eligible(item) or not decision.thesis_title:
        return False
    if item.thesis_title_confidence in {"High", "Manual"}:
        return False
    if _is_confirmed(catalog_record or {}, "title_confirmed"):
        return False
    return True


def interactive_check_plan(
    item: ProcessedStudent,
    decision: StudentDecision,
    *,
    overview_workbook_provided: bool = False,
    include_post_snr_follow_up: bool = False,
    catalog_record: dict[str, str] | None = None,
) -> ManualCheckFlags:
    """Which separate checks to run for this student in the terminal."""
    record = catalog_record or {}
    snr = overview_snr_needs_review(
        item,
        overview_workbook_provided=overview_workbook_provided,
    )
    thesis_pdf = thesis_pdf_needs_review(item, record)
    revision = revision_needs_review(item, record)
    title = title_needs_review(item) or saved_title_needs_confirmation(
        item,
        decision,
        record,
    )

    if include_post_snr_follow_up and not snr:
        if thesis_pdf_needs_review(item, record):
            thesis_pdf = True
        if revision_needs_review(item, record):
            revision = True
        if not decision.thesis_title and delivery_package_is_eligible(item):
            if item.thesis_title_confidence not in {"High", "Manual"}:
                title = True

    return ManualCheckFlags(
        snr=snr,
        thesis_pdf=thesis_pdf,
        revision=revision,
        title=title,
    )


def planned_check_labels(
    item: ProcessedStudent,
    decision: StudentDecision,
    *,
    overview_workbook_provided: bool = False,
    catalog_record: dict[str, str] | None = None,
) -> list[str]:
    """Labels shown up front, including checks queued after SNR is corrected."""
    return student_open_checks(
        item,
        decision,
        overview_workbook_provided=overview_workbook_provided,
        catalog_record=catalog_record,
    ).labels()


def student_open_checks(
    item: ProcessedStudent,
    decision: StudentDecision,
    *,
    overview_workbook_provided: bool = False,
    catalog_record: dict[str, str] | None = None,
) -> ManualCheckFlags:
    """Open manual checks before saved PDF/title choices are applied to the student."""
    initial = interactive_check_plan(
        item,
        decision,
        overview_workbook_provided=overview_workbook_provided,
        catalog_record=catalog_record,
    )
    if not initial.snr:
        return initial

    follow_up = interactive_check_plan(
        item,
        decision,
        overview_workbook_provided=overview_workbook_provided,
        include_post_snr_follow_up=True,
        catalog_record=catalog_record,
    )
    return ManualCheckFlags(
        snr=follow_up.snr,
        thesis_pdf=initial.thesis_pdf or follow_up.thesis_pdf,
        revision=initial.revision or follow_up.revision,
        title=initial.title or follow_up.title,
    )


def student_needs_interactive_prompt(
    item: ProcessedStudent,
    decision: StudentDecision,
    *,
    overview_workbook_provided: bool = False,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    """Students to walk through in the terminal during a normal run."""
    return student_open_checks(
        item,
        decision,
        overview_workbook_provided=overview_workbook_provided,
        catalog_record=catalog_record,
    ).any


def students_needing_interactive_prompt(
    students: list[ProcessedStudent],
    store: ManualDecisionStore,
    *,
    overview_workbook_provided: bool = False,
) -> list[ProcessedStudent]:
    return [
        item
        for item in students
        if student_needs_interactive_prompt(
            item,
            store.get(item.student.canvas_id),
            overview_workbook_provided=overview_workbook_provided,
            catalog_record=store.students.get(str(item.student.canvas_id), {}),
        )
    ]


def apply_saved_delivery_decisions(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    changed = False
    if apply_saved_pdf_decisions(item, decision, catalog_record):
        changed = True
    if apply_saved_title_decision(item, decision, catalog_record):
        changed = True
    return changed


def should_apply_saved_title(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None,
) -> bool:
    """Apply YAML title only when the user confirmed or edited it — not auto-catalog seed."""
    if not decision.thesis_title:
        return False
    title = normalize_manual_thesis_title(decision.thesis_title)
    if not title:
        return False

    record = catalog_record or {}
    if clean_text(record.get("title_confirmed", "")).lower() in {"true", "yes", "1"}:
        return True

    extracted = normalize_manual_thesis_title(record.get("extracted_title", ""))
    if extracted and title != extracted:
        return True

    if (
        item.thesis_title_confidence == "High"
        and extracted
        and title == extracted
    ):
        return True

    return False


def apply_saved_title_decision(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    if not should_apply_saved_title(item, decision, catalog_record):
        return False
    title = normalize_manual_thesis_title(decision.thesis_title)
    if not title:
        return False
    item.thesis_title = title
    item.thesis_title_confidence = "Manual"
    item.thesis_title_method = "manual"
    item.thesis_title_detail = "Manually entered during interactive review."
    item.thesis_title_needs_review = False
    return True


def _candidate_labels(item: ProcessedStudent) -> list[tuple[str, Path | None, dict | None]]:
    pdf = item.pdf_selection
    if pdf is None:
        return []
    if pdf.pdf_source == "canvas" and pdf.candidate_attachments:
        labels: list[tuple[str, Path | None, dict | None]] = []
        for attachment in pdf.candidate_attachments:
            labels.append((attachment_label(attachment), None, attachment))
        return labels
    return [(path.name, path, None) for path in pdf.candidates]


def _submission_candidate_labels(
    item: ProcessedStudent,
    submission_raw: dict | None,
) -> list[tuple[str, Path | None, dict | None]]:
    """PDF choices on one submission slot (not the combined cross-attempt list)."""
    pdf = item.pdf_selection
    if pdf is None:
        return []
    if pdf.pdf_source == "canvas":
        attachments = current_pdf_attachments_from_submission_raw(submission_raw)
        return [
            (attachment_label(attachment), None, attachment)
            for attachment in attachments
        ]

    from thesis2lib.pdf_matching import attachment_names_from_submission

    names = {
        name.casefold()
        for name in attachment_names_from_submission(submission_raw)
        if name
    }
    if not names:
        return []
    labels: list[tuple[str, Path | None, dict | None]] = []
    seen: set[str] = set()
    for path in pdf.candidates:
        key = path.name.casefold()
        if key in names and key not in seen:
            labels.append((path.name, path, None))
            seen.add(key)
    return labels


def _final_submission_candidate_labels(
    item: ProcessedStudent,
) -> list[tuple[str, Path | None, dict | None]]:
    return _submission_candidate_labels(item, _final_submission_raw(item))


def _print_numbered_pdf_options(
    candidates: list[tuple[str, Path | None, dict | None]],
    *,
    pdf: PdfSelection | None,
    current_thesis_label: str = "",
    current_revision_labels: list[str] | None = None,
) -> None:
    print("Available PDFs:")
    if not candidates:
        print("  (none found on this submission)")
        return
    revision_names = {
        Path(name).name.casefold()
        for name in (current_revision_labels or [])
        if name and name != "(none)"
    }
    thesis_name = Path(current_thesis_label).name.casefold() if current_thesis_label else ""
    for index, (label, _, _) in enumerate(candidates, start=1):
        flags: list[str] = []
        label_name = Path(label).name.casefold()
        if revision_document_filename(label):
            flags.append("revision-like filename")
        if thesis_name and label_name == thesis_name:
            flags.append("current thesis pick")
        elif pdf and pdf.selected_attachment and attachment_label(pdf.selected_attachment) == label:
            flags.append("current thesis pick")
        elif pdf and pdf.selected_path and pdf.selected_path.name == label:
            flags.append("current thesis pick")
        if label_name in revision_names:
            flags.append("current revision pick")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        print(f"  [{index}] {label}{suffix}")


def _match_label(label: str, candidates: list[tuple[str, Path | None, dict | None]]) -> int | None:
    target = label.strip().casefold()
    for index, (candidate_label, _, _) in enumerate(candidates, start=1):
        if candidate_label.casefold() == target:
            return index
        if Path(candidate_label).name.casefold() == Path(label).name.casefold():
            return index
    return None


def _apply_thesis_choice(
    item: ProcessedStudent,
    choice: tuple[str, Path | None, dict | None],
) -> None:
    pdf = item.pdf_selection
    if pdf is None:
        return
    label, path, attachment = choice
    pdf.status = "Matched"
    pdf.confidence = "Manual"
    pdf.needs_review = False
    pdf.detail = f"Manually selected thesis PDF: {label}."
    if attachment is not None:
        pdf.selected_attachment = attachment
        pdf.selected_path = None
    else:
        pdf.selected_path = path
        pdf.selected_attachment = None


def _apply_revision_choice(
    item: ProcessedStudent,
    choice: tuple[str, Path | None, dict | None] | None,
) -> None:
    pdf = item.pdf_selection
    if pdf is None:
        return
    if choice is None:
        pdf.revision_documents = []
        return
    label, path, attachment = choice
    if attachment is not None:
        pdf.revision_documents = [attachment_path_label(attachment)]
    elif path is not None:
        pdf.revision_documents = [path]
    else:
        pdf.revision_documents = [Path(label)]


def should_apply_saved_thesis_pdf(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None,
) -> bool:
    """Apply YAML thesis PDF only when confirmed or explicitly edited — not catalog seed."""
    if not decision.thesis_pdf:
        return False
    record = catalog_record or {}
    if _is_confirmed(record, "thesis_pdf_confirmed"):
        return True
    selected = clean_text(record.get("selected_pdf", ""))
    if selected and decision.thesis_pdf != selected:
        return True
    pdf = item.pdf_selection
    if (
        pdf
        and pdf.confidence == "High"
        and selected
        and decision.thesis_pdf == selected
    ):
        return True
    return False


def should_apply_saved_revision(
    decision: StudentDecision,
    catalog_record: dict[str, str] | None,
) -> bool:
    """Apply YAML revision PDF only when confirmed or explicitly edited."""
    if not decision.revision_pdf:
        return False
    record = catalog_record or {}
    if _is_confirmed(record, "revision_confirmed"):
        return True
    detected = clean_text(record.get("detected_revision", ""))
    if not detected:
        return False
    if decision.revision_pdf.lower() in {"none", "n/a", "no"}:
        return detected != "(none)"
    return decision.revision_pdf != detected


def apply_saved_pdf_decisions(
    item: ProcessedStudent,
    decision: StudentDecision,
    catalog_record: dict[str, str] | None = None,
) -> bool:
    apply_thesis = should_apply_saved_thesis_pdf(item, decision, catalog_record)
    apply_revision = should_apply_saved_revision(decision, catalog_record)
    if not apply_thesis and not apply_revision:
        return False

    candidates = _candidate_labels(item)
    changed = False
    if apply_thesis and decision.thesis_pdf and candidates:
        index = _match_label(decision.thesis_pdf, candidates)
        if index is not None:
            _apply_thesis_choice(item, candidates[index - 1])
            changed = True

    if apply_revision and decision.revision_pdf and candidates:
        if decision.revision_pdf.lower() in {"none", "n/a", "no"}:
            _apply_revision_choice(item, None)
            changed = True
        else:
            index = _match_label(decision.revision_pdf, candidates)
            if index is not None:
                _apply_revision_choice(item, candidates[index - 1])
                changed = True
    return changed


def apply_saved_overview_snr(
    item: ProcessedStudent,
    decision: StudentDecision,
    overview_workbook: OverviewWorkbook | None,
) -> bool:
    snr = normalize_identifier(decision.overview_snr)
    if not snr or overview_workbook is None:
        return False
    refresh_student_overview_data(item, overview_workbook, snr)
    item.overview_snr = normalize_identifier(item.overview_snr) or snr
    return True


def refresh_student_package_status(
    item: ProcessedStudent,
    *,
    assessment_workbook_provided: bool,
    overview_workbook_provided: bool,
) -> None:
    item.package_status, item.details = determine_package_status(
        item.student.course_result,
        item.final_attempt,
        item.pdf_selection,
        item.individual_assessment_found,
        item.joint_assessment_found,
        assessment_workbook_provided,
        overview_workbook_provided,
        item.archive_metadata,
    )


def _selected_thesis_pdf_label(item: ProcessedStudent) -> str:
    pdf = item.pdf_selection
    if pdf is None:
        return "(none)"
    if pdf.selected_path is not None:
        return pdf.selected_path.name
    if pdf.selected_attachment is not None:
        return attachment_label(pdf.selected_attachment)
    return "(none)"


def students_for_decisions_catalog(
    students: list[ProcessedStudent],
) -> list[ProcessedStudent]:
    """Passed students with a matched thesis PDF — listed in manual_decisions.yml."""
    return [item for item in students if delivery_package_is_eligible(item)]


def catalog_canvas_url(
    item: ProcessedStudent,
    context: CanvasReviewContext | None,
) -> str:
    """SpeedGrader URL for the student's final submission."""
    if context is None or not item.final_attempt:
        return ""
    assignment_id = assignment_id_for_final_attempt(
        item.final_attempt,
        first_assignment_id=context.first_assignment_id,
        second_assignment_id=context.second_assignment_id,
    )
    if assignment_id is None:
        return ""
    return canvas_speedgrader_url(
        context.canvas_url,
        context.course_id,
        assignment_id,
        item.student.canvas_id,
    )


def merge_catalog_record(
    item: ProcessedStudent,
    existing: dict[str, str] | None,
    *,
    canvas_context: CanvasReviewContext | None = None,
) -> dict[str, str]:
    """Merge auto-extracted metadata into a manual-decisions row."""
    from thesis2lib.delivery import resolve_archive_student_number, resolve_library_student_name

    prior = dict(existing or {})
    extracted = clean_text(item.thesis_title)
    existing_title = clean_text(prior.get("thesis_title", ""))
    previous_extracted = clean_text(prior.get("extracted_title", ""))

    if not existing_title:
        thesis_title = extracted
    elif existing_title == previous_extracted:
        thesis_title = extracted
    else:
        thesis_title = existing_title

    record: dict[str, str] = {
        "student_name": resolve_library_student_name(item),
        "canvas_url": catalog_canvas_url(item, canvas_context),
        "snr": resolve_archive_student_number(item),
        "selected_pdf": _selected_thesis_pdf_label(item),
        "detected_revision": _revision_documents_label(item),
        "extracted_title": extracted,
        "title_confidence": clean_text(item.thesis_title_confidence),
        "thesis_title": thesis_title,
    }
    for field in ("overview_snr", "thesis_pdf", "revision_pdf"):
        value = clean_text(prior.get(field, ""))
        if value:
            record[field] = value
    for field in (
        "title_confirmed",
        "thesis_pdf_confirmed",
        "revision_confirmed",
    ):
        value = clean_text(prior.get(field, ""))
        if value:
            record[field] = value
    return record


def sync_manual_decisions_catalog(
    store: ManualDecisionStore,
    students: list[ProcessedStudent],
    *,
    canvas_context: CanvasReviewContext | None = None,
) -> tuple[int, Path]:
    """Write all delivery-ready theses and extracted titles into manual_decisions.yml."""
    catalog = students_for_decisions_catalog(students)
    for item in catalog:
        key = str(item.student.canvas_id)
        store.students[key] = merge_catalog_record(
            item,
            store.students.get(key),
            canvas_context=canvas_context,
        )
    store.persist()
    return len(catalog), store.path


def _revision_documents_label(item: ProcessedStudent) -> str:
    pdf = item.pdf_selection
    if pdf is None or not pdf.revision_documents:
        return "(none)"
    names = [
        doc.name if hasattr(doc, "name") else str(doc)
        for doc in pdf.revision_documents
    ]
    return ", ".join(names)


def _print_manual_check_header(item: ProcessedStudent, labels: list[str]) -> None:
    print()
    print("=" * 72)
    print(f"Student: {item.student.name}  (Canvas ID {item.student.canvas_id})")
    print(f"Final attempt: {item.final_attempt or 'Unknown'}")
    print(f"Open issues: {', '.join(labels)}")


def _refresh_title_from_selection(
    item: ProcessedStudent,
    output_folder: Path,
    client: object | None,
) -> None:
    """Re-extract the thesis title after the user picks a different PDF."""
    if not delivery_package_is_eligible(item) or item.pdf_selection is None:
        return

    from thesis2lib.library_delivery import cached_thesis_pdf_path, thesis_pdf_cache_dir
    from thesis2lib.package_writer import write_thesis_from_selection

    pdf = item.pdf_selection
    selected_name = ""
    if pdf.selected_path is not None:
        selected_name = pdf.selected_path.name
    elif pdf.selected_attachment is not None:
        selected_name = attachment_label(pdf.selected_attachment)

    cache_path = cached_thesis_pdf_path(
        thesis_pdf_cache_dir(output_folder),
        item.student.canvas_id,
    )
    try:
        write_thesis_from_selection(pdf, cache_path, True, client)
        result = extract_thesis_title_result(
            cache_path,
            student_name=item.student.name,
            overview_title=(
                item.archive_metadata.thesis_title
                if item.archive_metadata and item.archive_metadata.thesis_title
                else ""
            ),
            filename=selected_name,
        )
    except Exception as exc:
        LOGGER.warning(
            "Could not refresh thesis title for %s after PDF change: %s",
            item.student.name,
            exc,
        )
        return

    item.thesis_title = result.title
    item.thesis_title_confidence = result.confidence
    item.thesis_title_method = result.method
    item.thesis_title_detail = result.detail
    item.thesis_title_needs_review = result.needs_review
    pdf.content_confidence = result.content_confidence
    pdf.content_detail = result.content_detail
    if not result.looks_like_thesis or not result.title:
        pdf.needs_review = True
        if not result.looks_like_thesis:
            pdf.confidence = "None"
            pdf.detail = result.content_detail or pdf.detail


def validate_cached_thesis_pdf(item: ProcessedStudent, cache_path: Path) -> None:
    pdf = item.pdf_selection
    if pdf is None or not cache_path.is_file():
        return

    selected_name = ""
    if pdf.selected_path is not None:
        selected_name = pdf.selected_path.name
    elif pdf.selected_attachment is not None:
        selected_name = attachment_label(pdf.selected_attachment)

    result = extract_thesis_title_result(
        cache_path,
        student_name=item.student.name,
        filename=selected_name,
    )
    pdf.content_confidence = result.content_confidence
    pdf.content_detail = result.content_detail
    if not result.looks_like_thesis:
        pdf.needs_review = True
        pdf.confidence = "None"
        pdf.detail = result.content_detail or pdf.detail


def _drain_buffered_stdin_lines() -> list[str]:
    """Return extra full lines already waiting on stdin (e.g. after a multi-line paste)."""
    if not sys.stdin.isatty():
        return []

    extra: list[str] = []
    while True:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0)
        except (OSError, ValueError):
            break
        if not ready:
            break
        line = sys.stdin.readline()
        if line == "":
            break
        stripped = line.strip()
        if stripped:
            extra.append(stripped)
    return extra


def _prompt_choice(
    prompt: str,
    *,
    allow_skip: bool = True,
    merge_pasted_lines: bool = False,
    accept_default: bool = False,
    default: str = "",
) -> str:
    while True:
        try:
            answer = input(prompt).strip()
        except EOFError:
            return "skip"

        extra = _drain_buffered_stdin_lines()
        if extra:
            if merge_pasted_lines:
                parts = [answer] if answer else []
                parts.extend(extra)
                answer = " ".join(parts).strip()
                if len(parts) > 1:
                    print(
                        "  (pasted title merged into one line)",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"  (ignored {len(extra)} extra pasted line(s) — use one line per answer)",
                    file=sys.stderr,
                )

        if not answer:
            if accept_default and default:
                return default
            if allow_skip:
                return "skip"
        return answer


def _commit_decision(
    store: ManualDecisionStore,
    canvas_id: str,
    decision: StudentDecision,
    *,
    field: str,
) -> None:
    """Write confirmed manual choices to Manual/manual_decisions.yml."""
    store.save(canvas_id, decision)
    key = str(canvas_id)
    record = dict(store.students.get(key, {}))
    if field == "thesis title":
        record["title_confirmed"] = "true"
    elif field == "thesis PDF":
        record["thesis_pdf_confirmed"] = "true"
    elif field == "revision PDF":
        record["revision_confirmed"] = "true"
    if field in {"thesis title", "thesis PDF", "revision PDF"}:
        store.students[key] = record
    store.persist()
    print(f"  Saved {field} → {store.path}", file=sys.stderr)


def _prompt_pdf_review(
    item: ProcessedStudent,
    decision: StudentDecision,
) -> tuple[StudentDecision, bool]:
    pdf = item.pdf_selection
    candidates = _final_submission_candidate_labels(item)
    print()
    print("--- Issue: Thesis PDF (final submission file) ---")
    print(f"Current pick: {_selected_thesis_pdf_label(item)}")
    if pdf:
        print(f"Detail: {pdf.detail}")
        if pdf.content_detail:
            print(f"Cover check: {pdf.content_detail}")
    print()
    _print_numbered_pdf_options(
        candidates,
        pdf=pdf,
        current_thesis_label=_selected_thesis_pdf_label(item),
    )

    default_thesis = decision.thesis_pdf or _selected_thesis_pdf_label(item)
    thesis_answer = _prompt_choice(
        f"Which file is the final thesis PDF? Enter number or filename"
        f"{f' [{default_thesis}]' if default_thesis else ''}: ",
        allow_skip=True,
        accept_default=bool(default_thesis),
        default=default_thesis,
    )
    if thesis_answer.lower() in {"skip", "s"}:
        return decision, False

    thesis_choice: tuple[str, Path | None, dict | None] | None = None
    if thesis_answer.isdigit():
        index = int(thesis_answer)
        if 1 <= index <= len(candidates):
            thesis_choice = candidates[index - 1]
    else:
        chosen = thesis_answer or default_thesis
        index = _match_label(chosen, candidates) if chosen else None
        if index is not None:
            thesis_choice = candidates[index - 1]

    if thesis_choice is not None:
        _apply_thesis_choice(item, thesis_choice)
        decision.thesis_pdf = thesis_choice[0]
        return decision, True

    return decision, False


def _prompt_revision_review(
    item: ProcessedStudent,
    decision: StudentDecision,
) -> tuple[StudentDecision, bool]:
    pdf = item.pdf_selection
    candidates = _submission_candidate_labels(item, _second_submission_raw(item))
    revision_label = _revision_documents_label(item)
    print()
    print("--- Issue: Revision PDF (2nd submission) ---")
    print(f"Current revision PDF: {revision_label}")
    print()
    _print_numbered_pdf_options(
        candidates,
        pdf=pdf,
        current_thesis_label=_selected_thesis_pdf_label(item),
        current_revision_labels=[revision_label],
    )
    default_revision = decision.revision_pdf
    revision_answer = _prompt_choice(
        "Which file is the revision document? Enter number, filename, or 'none'"
        f"{f' [{default_revision}]' if default_revision else ''}: ",
        allow_skip=True,
        accept_default=bool(default_revision),
        default=default_revision,
    )
    if revision_answer.lower() in {"skip", "s"}:
        return decision, False
    if revision_answer.lower() in {"none", "n", "no", ""} and not default_revision:
        _apply_revision_choice(item, None)
        decision.revision_pdf = "none"
        return decision, True

    chosen = revision_answer or default_revision
    if chosen and chosen.lower() in {"none", "n", "no"}:
        _apply_revision_choice(item, None)
        decision.revision_pdf = "none"
        return decision, True

    index = None
    if chosen.isdigit():
        idx = int(chosen)
        if 1 <= idx <= len(candidates):
            index = idx
    else:
        index = _match_label(chosen, candidates)
    if index is not None:
        _apply_revision_choice(item, candidates[index - 1])
        decision.revision_pdf = candidates[index - 1][0]
        return decision, True
    return decision, False


def _prompt_overview_snr_review(
    item: ProcessedStudent,
    decision: StudentDecision,
    overview_workbook: OverviewWorkbook,
) -> tuple[StudentDecision, bool]:
    archive = item.archive_metadata
    print()
    print("--- Issue: Overview SNR (workbook row link) ---")
    print(f"Canvas name: {item.student.name}")
    if archive and archive.student_name:
        print(f"Overview name: {archive.student_name}")
    print(
        "Current SNR: "
        f"{item.overview_snr or item.student.sis_user_id or '(none)'}"
    )
    print(
        "Archive info found: "
        f"{'Yes' if archive and archive.found else 'No'}"
    )
    if archive and archive.missing_fields:
        snr_fields = [field for field in archive.missing_fields if "snr" in field.lower() or field == "sis_user_id"]
        if snr_fields:
            print(f"Missing SNR-related fields: {', '.join(snr_fields)}")

    default_snr = normalize_identifier(decision.overview_snr or item.overview_snr)
    answer = _prompt_choice(
        "Enter the correct overview SNR"
        f"{f' [{default_snr}]' if default_snr else ''}: ",
        allow_skip=True,
        accept_default=bool(default_snr),
        default=default_snr,
    )
    if answer.lower() in {"skip", "s"}:
        return decision, False

    snr = normalize_identifier(answer)
    if not snr:
        return decision, False
    if not overview_has_snr(overview_workbook, snr):
        print(
            f"  WARNING: SNR {snr} was not found in the overview workbook.",
            file=sys.stderr,
        )
    decision.overview_snr = snr
    return decision, True


def _prompt_title_review(
    item: ProcessedStudent,
    decision: StudentDecision,
) -> tuple[StudentDecision, bool]:
    print()
    print("--- Issue: Thesis title (cover page) ---")
    print(f"Extracted title: {item.thesis_title or '(empty)'}")
    print(f"Confidence: {item.thesis_title_confidence or 'None'}")
    if item.thesis_title_detail:
        print(f"Detail: {item.thesis_title_detail}")
    default_title = decision.thesis_title or ""
    archive = item.archive_metadata
    if not default_title and archive and archive.thesis_title:
        default_title = normalize_manual_thesis_title(archive.thesis_title)
    if not default_title and item.thesis_title and item.thesis_title.lower() != "thesis":
        default_title = normalize_manual_thesis_title(item.thesis_title)
    answer = _prompt_choice(
        f"Enter the thesis title"
        f"{f' [{default_title}]' if default_title else ''}: ",
        allow_skip=True,
        merge_pasted_lines=True,
        accept_default=bool(default_title),
        default=default_title,
    )
    if answer.lower() in {"skip", "s"}:
        return decision, False
    title = normalize_manual_thesis_title(answer)
    if not title:
        print(
            "  Could not save thesis title (empty after cleanup). "
            "Type the title again or press Enter on the [bracket] default.",
            file=sys.stderr,
        )
        return decision, False
    item.thesis_title = title
    item.thesis_title_confidence = "Manual"
    item.thesis_title_method = "manual"
    item.thesis_title_detail = "Manually entered during interactive review."
    item.thesis_title_needs_review = False
    decision.thesis_title = title
    return decision, True


def review_manual_decisions(
    students: list[ProcessedStudent],
    output_folder: Path,
    *,
    interactive: bool,
    decisions_file: Path | None,
    client: object | None,
    assessment_workbook_provided: bool,
    overview_workbook_provided: bool,
    overview_workbook: OverviewWorkbook | None = None,
    canvas_context: CanvasReviewContext | None = None,
) -> tuple[int, int]:
    """Return (students handled, students that needed a manual check)."""
    store = load_manual_decision_store(output_folder, decisions_file)
    resolved = 0

    for item in students:
        key = str(item.student.canvas_id)
        decision = store.get(item.student.canvas_id)
        catalog_record = store.students.get(key, {})
        changed = False
        if apply_saved_overview_snr(item, decision, overview_workbook):
            changed = True
        if apply_saved_delivery_decisions(item, decision, catalog_record):
            changed = True
        if changed:
            resolved += 1
            refresh_student_package_status(
                item,
                assessment_workbook_provided=assessment_workbook_provided,
                overview_workbook_provided=overview_workbook_provided,
            )

    catalog_count, catalog_path = sync_manual_decisions_catalog(
        store,
        students,
        canvas_context=canvas_context,
    )
    if catalog_count:
        LOGGER.info(
            "Updated title catalog for %d student(s) in %s",
            catalog_count,
            catalog_path,
        )

    to_review = students_needing_interactive_prompt(
        students,
        store,
        overview_workbook_provided=overview_workbook_provided,
    )
    flagged_count = len(to_review)

    if interactive and to_review:
        if not sys.stdin.isatty():
            for item in to_review:
                LOGGER.warning(
                    "Manual review needed for %s but stdin is not interactive.",
                    item.student.name,
                )
        else:
            sys.stderr.write(
                f"\n  {flagged_count} student(s) need a manual check.\n"
            )
            sys.stderr.flush()
            check_progress = ConsoleProgress(flagged_count, "Manual checks")

            for index, item in enumerate(to_review, start=1):
                sys.stderr.write(
                    f"\n  Manual check {index}/{flagged_count}: "
                    f"{item.student.name}\n"
                )
                sys.stderr.flush()
                check_progress.set(index - 1, item.student.name[:36])

                decision = store.get(item.student.canvas_id)
                catalog_record = store.students.get(str(item.student.canvas_id), {})
                _print_manual_check_header(
                    item,
                    planned_check_labels(
                        item,
                        decision,
                        overview_workbook_provided=overview_workbook_provided,
                        catalog_record=catalog_record,
                    ),
                )
                show_canvas_submission(item, canvas_context)

                snr_was_open = overview_snr_needs_review(
                    item,
                    overview_workbook_provided=overview_workbook_provided,
                )

                if overview_workbook is not None and snr_was_open:
                    decision, snr_confirmed = _prompt_overview_snr_review(
                        item,
                        decision,
                        overview_workbook,
                    )
                    if decision.overview_snr:
                        refresh_student_overview_data(
                            item,
                            overview_workbook,
                            decision.overview_snr,
                        )
                        refresh_student_package_status(
                            item,
                            assessment_workbook_provided=assessment_workbook_provided,
                            overview_workbook_provided=overview_workbook_provided,
                        )
                        _refresh_title_from_selection(item, output_folder, client)
                    if snr_confirmed:
                        _commit_decision(
                            store,
                            item.student.canvas_id,
                            decision,
                            field="overview SNR",
                        )

                plan = interactive_check_plan(
                    item,
                    decision,
                    overview_workbook_provided=overview_workbook_provided,
                    include_post_snr_follow_up=snr_was_open,
                    catalog_record=catalog_record,
                )

                if plan.thesis_pdf:
                    decision, pdf_confirmed = _prompt_pdf_review(item, decision)
                    if pdf_confirmed:
                        _commit_decision(
                            store,
                            item.student.canvas_id,
                            decision,
                            field="thesis PDF",
                        )
                    refresh_student_package_status(
                        item,
                        assessment_workbook_provided=assessment_workbook_provided,
                        overview_workbook_provided=overview_workbook_provided,
                    )
                    _refresh_title_from_selection(item, output_folder, client)
                    plan = interactive_check_plan(
                        item,
                        decision,
                        overview_workbook_provided=overview_workbook_provided,
                        include_post_snr_follow_up=snr_was_open,
                        catalog_record=catalog_record,
                    )

                if plan.revision:
                    decision, revision_confirmed = _prompt_revision_review(
                        item,
                        decision,
                    )
                    if revision_confirmed:
                        _commit_decision(
                            store,
                            item.student.canvas_id,
                            decision,
                            field="revision PDF",
                        )
                    refresh_student_package_status(
                        item,
                        assessment_workbook_provided=assessment_workbook_provided,
                        overview_workbook_provided=overview_workbook_provided,
                    )

                plan = interactive_check_plan(
                    item,
                    decision,
                    overview_workbook_provided=overview_workbook_provided,
                    include_post_snr_follow_up=snr_was_open,
                    catalog_record=catalog_record,
                )
                if plan.title:
                    decision, title_confirmed = _prompt_title_review(item, decision)
                    if title_confirmed:
                        _commit_decision(
                            store,
                            item.student.canvas_id,
                            decision,
                            field="thesis title",
                        )

                resolved += 1
                check_progress.advance(item.student.name[:36])

            check_progress.finish(f"{len(to_review)} reviewed")

    if not interactive:
        pending_snr = sum(
            1
            for item in students
            if overview_snr_needs_review(
                item,
                overview_workbook_provided=overview_workbook_provided,
            )
        )
        pending_pdf = sum(
            1
            for item in students
            if thesis_pdf_needs_review(
                item,
                store.students.get(str(item.student.canvas_id), {}),
            )
        )
        pending_revision = sum(
            1
            for item in students
            if revision_needs_review(
                item,
                store.students.get(str(item.student.canvas_id), {}),
            )
        )
        pending_title = sum(1 for item in students if title_needs_review(item))
        pending = sum(
            1
            for item in students
            if student_needs_interactive_prompt(
                item,
                store.get(item.student.canvas_id),
                overview_workbook_provided=overview_workbook_provided,
                catalog_record=store.students.get(str(item.student.canvas_id), {}),
            )
        )
        if pending:
            LOGGER.warning(
                "%d student(s) still need a manual check. Run again in a terminal "
                "(remove --no-interactive-review if you used it).",
                pending,
            )
        if pending_snr:
            LOGGER.warning(
                "%d student(s) still need an overview SNR link.",
                pending_snr,
            )
        if pending_pdf:
            LOGGER.warning(
                "%d student(s) still need a thesis PDF choice.",
                pending_pdf,
            )
        if pending_revision:
            LOGGER.warning(
                "%d student(s) still need a revision document choice.",
                pending_revision,
            )
        if pending_title:
            LOGGER.warning(
                "%d student(s) still need a thesis title.",
                pending_title,
            )

    return resolved, flagged_count


def review_pdf_selections(
    students: list[ProcessedStudent],
    output_folder: Path,
    *,
    interactive: bool,
    decisions_file: Path | None,
    assessment_workbook_provided: bool,
    overview_workbook_provided: bool,
) -> tuple[int, int]:
    return review_manual_decisions(
        students,
        output_folder,
        interactive=interactive,
        decisions_file=decisions_file,
        client=None,
        assessment_workbook_provided=assessment_workbook_provided,
        overview_workbook_provided=overview_workbook_provided,
    )


def review_thesis_titles(
    students: list[ProcessedStudent],
    output_folder: Path,
    *,
    interactive: bool,
    decisions_file: Path | None,
) -> int:
    del interactive, decisions_file
    return 0
