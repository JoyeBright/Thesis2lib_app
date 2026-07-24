"""Write flat final-thesis PDFs for library Open Access delivery."""

from __future__ import annotations

import shutil
from pathlib import Path

from thesis2lib.canvas_client import CanvasClient
from thesis2lib.delivery import (
    library_author_name,
    open_access_pdf_path_for_item,
    open_access_root,
    resolve_library_student_name,
    unique_delivery_path,
)
from thesis2lib.library_delivery import cached_thesis_pdf_path, thesis_pdf_cache_dir
from thesis2lib.models import ProcessedStudent
from thesis2lib.package_writer import delivery_package_is_eligible
from thesis2lib.progress import ConsoleProgress


def write_open_access_pdf(
    item: ProcessedStudent,
    open_access_folder: Path,
    cache_dir: Path,
    overwrite: bool,
) -> Path | None:
    if not delivery_package_is_eligible(item):
        return None
    if not item.thesis_title:
        return None

    cache_path = cached_thesis_pdf_path(cache_dir, item.student.canvas_id)
    if not cache_path.exists():
        return None

    destination = unique_delivery_path(
        open_access_pdf_path_for_item(open_access_folder, item)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        return destination
    shutil.copy2(cache_path, destination)
    return destination


def create_open_access_pdfs(
    students: list[ProcessedStudent],
    output_folder: Path,
    semester: str,
    programme: str,
    overwrite: bool,
) -> int:
    folder = open_access_root(output_folder, semester, programme)
    folder.mkdir(parents=True, exist_ok=True)
    cache_dir = thesis_pdf_cache_dir(output_folder)

    eligible = [
        item
        for item in students
        if delivery_package_is_eligible(item) and item.thesis_title
    ]
    progress = ConsoleProgress(len(eligible), "Open Access PDFs") if eligible else None

    created = 0
    for item in eligible:
        if write_open_access_pdf(item, folder, cache_dir, overwrite):
            created += 1
        if progress:
            progress.advance(library_author_name(resolve_library_student_name(item)))

    if progress:
        progress.finish()
    return created
