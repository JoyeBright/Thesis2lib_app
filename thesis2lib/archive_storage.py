"""Download or copy all submission PDFs into flat programme archive folders."""

from __future__ import annotations

import shutil
from pathlib import Path

from thesis2lib.canvas_attachments import (
    attachment_download_url,
    attachment_label,
    is_pdf_attachment,
)
from thesis2lib.canvas_client import CanvasClient
from thesis2lib.delivery import (
    archive_storage_root,
    attempt_folder_name,
    delivery_name_slug,
    resolve_archive_student_number,
)
from thesis2lib.download_pool import (
    DEFAULT_DOWNLOAD_WORKERS,
    DownloadJob,
    run_parallel_downloads,
)
from thesis2lib.models import ProcessedStudent
from thesis2lib.pdf_matching import find_matched_pdfs
from thesis2lib.progress import ConsoleProgress
from thesis2lib.submissions import has_real_submission
from thesis2lib.utils import download_overwrite, sanitize_folder_name, should_fetch_download


def archive_attempt_has_files(item: ProcessedStudent, attempt: str) -> bool:
    submission = item.first_submission if attempt == "First" else item.second_submission
    if submission is None:
        return False
    return has_real_submission(submission.raw)


def all_submission_pdf_attachments(submission: dict | None) -> list[dict]:
    if not submission:
        return []

    seen_ids: set[str | int] = set()
    attachments: list[dict] = []

    def add(raw: dict) -> None:
        attachment_id = raw.get("id")
        if attachment_id in seen_ids:
            return
        if not is_pdf_attachment(raw):
            return
        seen_ids.add(attachment_id)
        attachments.append(raw)

    for raw in submission.get("attachments") or []:
        if isinstance(raw, dict):
            add(raw)

    for entry in submission.get("submission_history") or []:
        if not isinstance(entry, dict):
            continue
        for raw in entry.get("attachments") or []:
            if isinstance(raw, dict):
                add(raw)

    return attachments


def student_file_prefix(item: ProcessedStudent) -> str:
    slug = delivery_name_slug(item.student.name)
    number = sanitize_folder_name(resolve_archive_student_number(item)) or "UnknownID"
    return f"{slug}_{number}"


def archive_destination_name(item: ProcessedStudent, filename: str) -> str:
    label = sanitize_folder_name(filename) or "document.pdf"
    if not label.lower().endswith(".pdf"):
        label = f"{label}.pdf"
    return f"{student_file_prefix(item)}_{label}"


def archive_target_path(destination_folder: Path, filename: str) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_folder_name(filename) or "document.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"
    return destination_folder / safe_name


def count_archive_files(
    students: list[ProcessedStudent],
    client: CanvasClient | None,
    first_pdf_pool: list[Path] | None,
    second_pdf_pool: list[Path] | None,
) -> int:
    total = 0
    for item in students:
        for attempt, pool in (("First", first_pdf_pool), ("Second", second_pdf_pool)):
            if not archive_attempt_has_files(item, attempt):
                continue
            submission = (
                item.first_submission if attempt == "First" else item.second_submission
            )
            assert submission is not None
            if client is not None:
                total += len(all_submission_pdf_attachments(submission.raw))
            elif pool is not None:
                from thesis2lib.pdf_matching import attachment_names_from_submission

                attachment_names = attachment_names_from_submission(submission.raw)
                total += len(
                    find_matched_pdfs(
                        pool,
                        item.student.sis_user_id,
                        item.student.canvas_id,
                        attachment_names,
                    )
                )
    return total


def collect_archive_download_jobs(
    students: list[ProcessedStudent],
    first_folder: Path,
    second_folder: Path,
    overwrite: bool,
    skip_existing_downloads: bool,
) -> tuple[list[DownloadJob], int]:
    jobs: list[DownloadJob] = []
    skipped = 0
    overwrite_download = download_overwrite(
        overwrite=overwrite,
        skip_existing=skip_existing_downloads,
    )

    for item in students:
        for attempt, folder in (("First", first_folder), ("Second", second_folder)):
            if not archive_attempt_has_files(item, attempt):
                continue
            submission = (
                item.first_submission if attempt == "First" else item.second_submission
            )
            assert submission is not None
            for attachment in all_submission_pdf_attachments(submission.raw):
                url = attachment_download_url(attachment)
                if not url:
                    continue
                name = archive_destination_name(item, attachment_label(attachment))
                target = archive_target_path(folder, name)
                if not should_fetch_download(
                    target,
                    overwrite=overwrite,
                    skip_existing=skip_existing_downloads,
                ):
                    skipped += 1
                    continue
                jobs.append(
                    DownloadJob(
                        url=url,
                        destination=target,
                        overwrite=overwrite_download,
                        label=name,
                    )
                )
    return jobs, skipped


def write_archive_attempt_local(
    item: ProcessedStudent,
    attempt: str,
    destination_folder: Path,
    overwrite: bool,
    skip_existing_downloads: bool,
    pdf_pool: list[Path] | None,
    progress: ConsoleProgress | None,
) -> int:
    if not archive_attempt_has_files(item, attempt) or pdf_pool is None:
        return 0

    from thesis2lib.pdf_matching import attachment_names_from_submission

    submission = item.first_submission if attempt == "First" else item.second_submission
    assert submission is not None
    written = 0
    attachment_names = attachment_names_from_submission(submission.raw)
    for source in find_matched_pdfs(
        pdf_pool,
        item.student.sis_user_id,
        item.student.canvas_id,
        attachment_names,
    ):
        name = archive_destination_name(item, source.name)
        target = archive_target_path(destination_folder, name)
        if not should_fetch_download(
            target,
            overwrite=overwrite,
            skip_existing=skip_existing_downloads,
        ):
            written += 1
            if progress:
                progress.advance(source.name)
            continue
        shutil.copy2(source, target)
        written += 1
        if progress:
            progress.advance(source.name)
    return written


def create_archive_storage(
    students: list[ProcessedStudent],
    output_folder: Path,
    overwrite: bool,
    client: CanvasClient | None = None,
    first_pdf_pool: list[Path] | None = None,
    second_pdf_pool: list[Path] | None = None,
    skip_existing_downloads: bool = False,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> int:
    archive_root = archive_storage_root(output_folder)
    first_folder = archive_root / attempt_folder_name("First")
    second_folder = archive_root / attempt_folder_name("Second")
    first_folder.mkdir(parents=True, exist_ok=True)
    second_folder.mkdir(parents=True, exist_ok=True)

    total_files = count_archive_files(students, client, first_pdf_pool, second_pdf_pool)
    progress = ConsoleProgress(total_files, "Archive PDFs") if total_files else None

    files_written = 0
    if client is not None:
        jobs, skipped = collect_archive_download_jobs(
            students,
            first_folder,
            second_folder,
            overwrite,
            skip_existing_downloads,
        )
        if progress:
            for _ in range(skipped):
                progress.advance("existing")
        files_written += skipped
        files_written += run_parallel_downloads(
            client,
            jobs,
            workers=download_workers,
            progress=progress,
        )
    else:
        for item in students:
            files_written += write_archive_attempt_local(
                item,
                "First",
                first_folder,
                overwrite,
                skip_existing_downloads,
                first_pdf_pool,
                progress,
            )
            files_written += write_archive_attempt_local(
                item,
                "Second",
                second_folder,
                overwrite,
                skip_existing_downloads,
                second_pdf_pool,
                progress,
            )

    if progress:
        progress.finish()

    return files_written
