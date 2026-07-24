"""Parallel Canvas attachment downloads."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from thesis2lib.canvas_client import CanvasClient, CanvasAPIError
from thesis2lib.progress import ConsoleProgress

LOGGER = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_WORKERS = max(1, min(6, (os.cpu_count() or 4)))


@dataclass(frozen=True)
class DownloadJob:
    url: str
    destination: Path
    overwrite: bool
    label: str = ""


def run_parallel_downloads(
    client: CanvasClient,
    jobs: list[DownloadJob],
    *,
    workers: int = DEFAULT_DOWNLOAD_WORKERS,
    progress: ConsoleProgress | None = None,
) -> int:
    """Download Canvas files concurrently. Returns count of attempted downloads."""
    if not jobs:
        return 0

    worker_count = max(1, min(workers, len(jobs)))
    if worker_count == 1:
        completed = 0
        for job in jobs:
            client.download_file(job.url, job.destination, overwrite=job.overwrite)
            completed += 1
            if progress:
                progress.advance(job.label[:36])
        return completed

    def _download(job: DownloadJob) -> DownloadJob:
        thread_client = CanvasClient(
            client.base_url,
            client.token,
            timeout=client.timeout,
            max_retries=client.max_retries,
        )
        thread_client.download_file(job.url, job.destination, overwrite=job.overwrite)
        return job

    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_download, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                future.result()
            except CanvasAPIError as exc:
                LOGGER.warning(
                    "Could not download %s: %s",
                    job.destination.name,
                    exc,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Unexpected download error for %s: %s",
                    job.destination.name,
                    exc,
                )
            else:
                completed += 1
            if progress:
                progress.advance(job.label[:36])

    return completed
