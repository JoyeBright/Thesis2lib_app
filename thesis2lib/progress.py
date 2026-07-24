"""Terminal progress for Thesis2Lib pipeline steps."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator


class ConsoleProgress:
    def __init__(self, total: int, label: str) -> None:
        self.total = max(total, 1)
        self.label = label
        self.current = 0

    def advance(self, detail: str = "") -> None:
        self.current = min(self.current + 1, self.total)
        self._render(detail)

    def set(self, current: int, detail: str = "") -> None:
        self.current = min(max(current, 0), self.total)
        self._render(detail)

    def _render(self, detail: str) -> None:
        percent = int(100 * self.current / self.total)
        width = 32
        filled = int(width * self.current / self.total)
        bar = "#" * filled + "-" * (width - filled)
        message = f"\r  {self.label} [{bar}] {self.current}/{self.total} ({percent}%)"
        if detail:
            message += f" {detail[:36]}"
        sys.stderr.write(message.ljust(100))
        sys.stderr.flush()

    def finish(self, detail: str = "") -> None:
        self.set(self.total, detail)
        sys.stderr.write("\n")
        sys.stderr.flush()


class StepProgress:
    def __init__(self, step: int, total: int, label: str) -> None:
        self.step = step
        self.total = total
        self.label = label
        self._finished = False

    def start(self, detail: str = "") -> None:
        message = f"[{self.step}/{self.total}] {self.label}"
        if detail:
            message += f" ({detail})"
        sys.stderr.write(message + "...\n")
        sys.stderr.flush()

    def finish(self, detail: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        message = f"[{self.step}/{self.total}] {self.label}"
        if detail:
            message += f" — {detail}"
        else:
            message += " — done"
        sys.stderr.write(message + "\n")
        sys.stderr.flush()


@contextmanager
def pipeline_step(
    step: int,
    total: int,
    label: str,
    *,
    start_detail: str = "",
) -> Iterator[StepProgress]:
    progress = StepProgress(step, total, label)
    progress.start(start_detail)
    try:
        yield progress
    except Exception:
        sys.stderr.write(f"[{step}/{total}] {label} — failed\n")
        sys.stderr.flush()
        raise
    else:
        if not progress._finished:
            progress.finish()


def count_pipeline_steps(
    *,
    report_only: bool,
    has_overview: bool,
    has_assessment: bool,
) -> int:
    total = 7  # submissions, students, titles, manual review, report, gradebook, graduates
    if has_overview:
        total += 1
    if has_assessment:
        total += 1
    if not report_only:
        total += 4  # delivery folders, archive, open access, SOP
    return total
