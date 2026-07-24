"""Prepare library delivery metadata from final thesis PDFs."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from thesis2lib.canvas_attachments import attachment_download_url
from thesis2lib.canvas_client import CanvasClient
from thesis2lib.download_pool import (
    DEFAULT_DOWNLOAD_WORKERS,
    DownloadJob,
    run_parallel_downloads,
)
from thesis2lib.models import ProcessedStudent
from thesis2lib.package_writer import (
    delivery_package_is_eligible,
    resolve_final_thesis_selection,
    write_thesis_from_selection,
)
from thesis2lib.progress import ConsoleProgress
from thesis2lib.thesis_title import TitleExtractionResult, extract_thesis_title_result
from thesis2lib.utils import download_overwrite, should_fetch_download

LOGGER = logging.getLogger(__name__)

DEFAULT_TITLE_WORKERS = max(1, min(8, (os.cpu_count() or 4)))


@dataclass(frozen=True)
class TitleJob:
    cache_path: str
    student_name: str
    overview_title: str
    selected_name: str
    alternate_paths: tuple[str, ...] = ()


def thesis_pdf_cache_dir(output_folder: Path) -> Path:
    return output_folder / ".thesis2lib_cache"


def cached_thesis_pdf_path(cache_dir: Path, canvas_id: str) -> Path:
    return cache_dir / f"{canvas_id}.pdf"


def cached_alternate_pdf_path(cache_dir: Path, canvas_id: str, attachment_id: str) -> Path:
    return cache_dir / f"{canvas_id}_{attachment_id}.pdf"


def _attachment_title_alternate_jobs(
    item: ProcessedStudent,
    cache_dir: Path,
    *,
    overwrite_download: bool,
    client: CanvasClient,
) -> tuple[list[Path], list[DownloadJob]]:
    """Download other final-submission PDFs to try when the selected file is not a thesis."""
    pdf = item.pdf_selection
    if pdf is None or not pdf.candidate_attachments:
        return [], []

    from thesis2lib.canvas_attachments import (
        attachment_download_url,
        attachment_label,
        attachment_path_label,
    )
    from thesis2lib.pdf_matching import is_strong_revision_document

    selected_id = str((pdf.selected_attachment or {}).get("id") or "")
    alternate_paths: list[Path] = []
    download_jobs: list[DownloadJob] = []

    for attachment in pdf.candidate_attachments:
        attachment_id = str(attachment.get("id") or "")
        if not attachment_id or attachment_id == selected_id:
            continue
        if is_strong_revision_document(attachment_path_label(attachment)):
            continue

        destination = cached_alternate_pdf_path(
            cache_dir,
            item.student.canvas_id,
            attachment_id,
        )
        alternate_paths.append(destination)
        if should_fetch_download(
            destination,
            overwrite=overwrite_download,
            skip_existing=False,
        ):
            download_jobs.append(
                DownloadJob(
                    url=attachment_download_url(attachment),
                    destination=destination,
                    overwrite=overwrite_download,
                    label=attachment_label(attachment)[:36],
                )
            )

    return alternate_paths, download_jobs


def _selected_pdf_name(item: ProcessedStudent) -> str:
    pdf = item.pdf_selection
    if pdf is None:
        return ""
    if pdf.selected_path is not None:
        return pdf.selected_path.name
    if pdf.selected_attachment is not None:
        from thesis2lib.canvas_attachments import attachment_label

        return attachment_label(pdf.selected_attachment)
    return ""


def _apply_title_result(item: ProcessedStudent, result: TitleExtractionResult) -> None:
    item.thesis_title = result.title
    item.thesis_title_confidence = result.confidence
    item.thesis_title_method = result.method
    item.thesis_title_detail = result.detail
    item.thesis_title_needs_review = result.needs_review

    pdf = item.pdf_selection
    if pdf is None:
        return

    pdf.content_confidence = result.content_confidence
    pdf.content_detail = result.content_detail
    if not result.looks_like_thesis or not result.title:
        pdf.needs_review = True
        if not result.looks_like_thesis:
            pdf.confidence = "None"
            pdf.detail = result.content_detail or pdf.detail


def _extract_title_job(job: TitleJob) -> TitleExtractionResult:
    alternates = [Path(path) for path in job.alternate_paths if Path(path).is_file()]
    return extract_thesis_title_result(
        Path(job.cache_path),
        student_name=job.student_name,
        overview_title=job.overview_title,
        filename=job.selected_name,
        alternate_paths=alternates,
    )


def populate_thesis_titles(
    students: list[ProcessedStudent],
    output_folder: Path,
    *,
    client: CanvasClient | None,
    use_canvas: bool,
    first_pdf_pool: list[Path] | None = None,
    second_pdf_pool: list[Path] | None = None,
    overwrite: bool = False,
    skip_existing_downloads: bool = False,
    workers: int = DEFAULT_TITLE_WORKERS,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> int:
    cache_dir = thesis_pdf_cache_dir(output_folder)
    cache_dir.mkdir(parents=True, exist_ok=True)

    eligible = [item for item in students if delivery_package_is_eligible(item)]
    if not eligible:
        return 0

    jobs: list[tuple[ProcessedStudent, TitleJob]] = []
    canvas_download_jobs: list[DownloadJob] = []
    download_progress = ConsoleProgress(len(eligible), "Caching thesis PDFs")

    for item in eligible:
        selection = resolve_final_thesis_selection(
            item,
            use_canvas=use_canvas,
            first_pdf_pool=first_pdf_pool,
            second_pdf_pool=second_pdf_pool,
        )
        if selection is None:
            download_progress.advance(item.student.name[:36])
            continue

        cache_path = cached_thesis_pdf_path(cache_dir, item.student.canvas_id)
        student_label = item.student.name[:36]
        try:
            if should_fetch_download(
                cache_path,
                overwrite=overwrite,
                skip_existing=skip_existing_downloads,
            ):
                overwrite_download = download_overwrite(
                    overwrite=overwrite,
                    skip_existing=skip_existing_downloads,
                )
                if selection.selected_path is not None:
                    write_thesis_from_selection(
                        selection,
                        cache_path,
                        overwrite_download,
                        client,
                    )
                    download_progress.advance(student_label)
                elif selection.selected_attachment is not None and client is not None:
                    url = attachment_download_url(selection.selected_attachment)
                    canvas_download_jobs.append(
                        DownloadJob(
                            url=url,
                            destination=cache_path,
                            overwrite=overwrite_download,
                            label=student_label,
                        )
                    )
                else:
                    raise RuntimeError(
                        "Canvas client is required to download thesis attachments."
                    )
            else:
                download_progress.advance(student_label)

            overwrite_download = download_overwrite(
                overwrite=overwrite,
                skip_existing=skip_existing_downloads,
            )
            overview_title = ""
            if item.archive_metadata and item.archive_metadata.thesis_title:
                overview_title = item.archive_metadata.thesis_title

            alternate_paths: list[Path] = []
            if client is not None:
                alternate_paths, alternate_jobs = _attachment_title_alternate_jobs(
                    item,
                    cache_dir,
                    overwrite_download=overwrite_download,
                    client=client,
                )
                canvas_download_jobs.extend(alternate_jobs)

            jobs.append(
                (
                    item,
                    TitleJob(
                        cache_path=str(cache_path),
                        student_name=item.student.name,
                        overview_title=overview_title,
                        selected_name=_selected_pdf_name(item),
                        alternate_paths=tuple(str(path) for path in alternate_paths),
                    ),
                )
            )
        except Exception as exc:
            logging.warning(
                "Could not prepare thesis PDF for %s: %s",
                item.student.name,
                exc,
            )
            item.thesis_title = ""
            item.thesis_title_confidence = "None"
            item.thesis_title_method = "error"
            item.thesis_title_detail = str(exc)
            item.thesis_title_needs_review = True
            download_progress.advance(student_label)

    if canvas_download_jobs and client is not None:
        run_parallel_downloads(
            client,
            canvas_download_jobs,
            workers=download_workers,
            progress=download_progress,
        )

    download_progress.finish(f"{len(jobs)} cached")

    if not jobs:
        return 0

    populated = 0
    title_progress = ConsoleProgress(len(jobs), "Thesis titles")
    worker_count = max(1, min(workers, len(jobs)))

    if worker_count == 1:
        for item, job in jobs:
            try:
                result = _extract_title_job(job)
                _apply_title_result(item, result)
                populated += 1
            except Exception as exc:
                logging.warning(
                    "Could not extract thesis title for %s: %s",
                    item.student.name,
                    exc,
                )
                item.thesis_title = ""
                item.thesis_title_confidence = "None"
                item.thesis_title_method = "error"
                item.thesis_title_detail = str(exc)
                item.thesis_title_needs_review = True
            title_progress.advance(item.student.name[:36])
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(_extract_title_job, job): item
                for item, job in jobs
            }
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    result = future.result()
                    _apply_title_result(item, result)
                    populated += 1
                except Exception as exc:
                    logging.warning(
                        "Could not extract thesis title for %s: %s",
                        item.student.name,
                        exc,
                    )
                    item.thesis_title = ""
                    item.thesis_title_confidence = "None"
                    item.thesis_title_method = "error"
                    item.thesis_title_detail = str(exc)
                    item.thesis_title_needs_review = True
                title_progress.advance(item.student.name[:36])

    title_progress.finish(f"{populated} extracted")
    return populated
