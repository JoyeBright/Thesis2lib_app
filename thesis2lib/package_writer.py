"""Write SOP student delivery packages to the output folder."""

from __future__ import annotations

import shutil
from pathlib import Path

from thesis2lib.assessment_writer import write_assessments_file
from thesis2lib.canvas_attachments import attachment_download_url, thesis_pdf_selected
from thesis2lib.canvas_client import CanvasClient
from thesis2lib.delivery import delivery_paths_for_student, sop_root
from thesis2lib.progress import ConsoleProgress
from thesis2lib.models import PdfSelection, ProcessedStudent


def delivery_package_is_eligible(item: ProcessedStudent) -> bool:
    """True when a thesis PDF can be delivered (passed + matched final submission)."""
    return (
        item.student.course_result == "Passed"
        and item.final_attempt is not None
        and item.pdf_selection is not None
        and item.pdf_selection.status == "Matched"
        and thesis_pdf_selected(item.pdf_selection)
    )


def pdf_package_is_eligible(item: ProcessedStudent) -> bool:
    return delivery_package_is_eligible(item)


def copy_thesis_pdf(
    source_pdf: Path,
    destination: Path,
    overwrite: bool,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        return destination
    shutil.copy2(source_pdf, destination)
    return destination


def resolve_final_thesis_selection(
    item: ProcessedStudent,
    *,
    use_canvas: bool,
    first_pdf_pool: list[Path] | None = None,
    second_pdf_pool: list[Path] | None = None,
) -> PdfSelection | None:
    """Pick the final-attempt thesis PDF for library or archive delivery."""
    if item.pdf_selection and thesis_pdf_selected(item.pdf_selection):
        return item.pdf_selection
    if not item.final_attempt:
        return None

    submission = (
        item.second_submission
        if item.final_attempt == "Second"
        else item.first_submission
    )
    if submission is None:
        return None

    from thesis2lib.canvas_attachments import select_canvas_attachments
    from thesis2lib.pdf_matching import attachment_names_from_submission, select_pdf_candidate

    attachment_names = attachment_names_from_submission(submission.raw)
    if use_canvas:
        attachments = submission.raw.get("attachments") if submission.raw else []
        return select_canvas_attachments(attachments if isinstance(attachments, list) else [])

    pdf_pool = second_pdf_pool if item.final_attempt == "Second" else first_pdf_pool
    if pdf_pool is not None:
        return select_pdf_candidate(
            pdf_pool,
            item.student.sis_user_id,
            item.student.canvas_id,
            attachment_names,
        )
    return None


def write_thesis_from_selection(
    selection: PdfSelection,
    destination: Path,
    overwrite: bool,
    client: CanvasClient | None,
) -> Path:
    if selection.selected_path is not None:
        return copy_thesis_pdf(selection.selected_path, destination, overwrite)
    if selection.selected_attachment is not None:
        if client is None:
            raise RuntimeError(
                "Canvas client is required to download thesis attachments."
            )
        url = attachment_download_url(selection.selected_attachment)
        return client.download_file(url, destination, overwrite=overwrite)
    raise RuntimeError("No thesis PDF source was selected for this student.")


def write_thesis_pdf(
    item: ProcessedStudent,
    destination: Path,
    overwrite: bool,
    client: CanvasClient | None,
) -> Path:
    pdf_selection = item.pdf_selection
    assert pdf_selection is not None
    return write_thesis_from_selection(pdf_selection, destination, overwrite, client)


def write_sop_package(
    item: ProcessedStudent,
    sop_folder: Path,
    overwrite: bool,
    client: CanvasClient | None = None,
    *,
    output_folder: Path | None = None,
    skip_existing_downloads: bool = False,
) -> Path | None:
    if not delivery_package_is_eligible(item):
        return None

    assert item.pdf_selection is not None
    assert thesis_pdf_selected(item.pdf_selection)

    paths = delivery_paths_for_student(sop_folder, item)
    from thesis2lib.utils import download_overwrite

    overwrite_thesis = download_overwrite(
        overwrite=overwrite,
        skip_existing=skip_existing_downloads,
    )

    cache_path = None
    if output_folder is not None:
        from thesis2lib.library_delivery import (
            cached_thesis_pdf_path,
            thesis_pdf_cache_dir,
        )

        cache_path = cached_thesis_pdf_path(
            thesis_pdf_cache_dir(output_folder),
            item.student.canvas_id,
        )

    if cache_path is not None and cache_path.is_file():
        copy_thesis_pdf(cache_path, paths.thesis_pdf, overwrite_thesis)
    else:
        write_thesis_pdf(
            item,
            paths.thesis_pdf,
            overwrite_thesis,
            client,
        )

    write_assessments_file(
        item,
        paths.folder,
        paths.student_number,
        overwrite,
        destination=paths.assessments,
    )

    return paths.folder


def create_sop_packages(
    students: list[ProcessedStudent],
    output_folder: Path,
    overwrite: bool,
    client: CanvasClient | None = None,
    *,
    skip_existing_downloads: bool = False,
) -> int:
    sop_folder = sop_root(output_folder)
    sop_folder.mkdir(parents=True, exist_ok=True)

    eligible = [item for item in students if delivery_package_is_eligible(item)]
    progress = ConsoleProgress(len(eligible), "SOP packages") if eligible else None

    created = 0
    for item in eligible:
        if write_sop_package(
            item,
            sop_folder,
            overwrite,
            client,
            output_folder=output_folder,
            skip_existing_downloads=skip_existing_downloads,
        ):
            created += 1
        if progress:
            progress.advance(item.student.name[:36])

    if progress:
        progress.finish()
    return created


# Backwards-compatible aliases
write_delivery_package = write_sop_package
create_packages = create_sop_packages
