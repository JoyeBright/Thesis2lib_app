"""Canvas submission detection and final attempt selection."""

from __future__ import annotations

from thesis2lib.utils import clean_text, parse_score


def _history_has_attachments(submission: dict) -> bool:
    for entry in submission.get("submission_history") or []:
        if isinstance(entry, dict) and entry.get("attachments"):
            return True
    return False


def has_real_submission(submission: dict | None) -> bool:
    """True only when Canvas shows an uploaded file, not a graded or dated empty shell."""
    if not submission:
        return False

    workflow = clean_text(submission.get("workflow_state")).casefold()
    if workflow == "unsubmitted":
        return False

    if submission.get("attachments"):
        return True

    return _history_has_attachments(submission)


def submission_is_graded(submission: dict | None) -> bool:
    """True when Canvas entered a score or rubric grade on this submission."""
    if not has_real_submission(submission):
        return False
    if parse_score(submission.get("score")) is not None:
        return True
    from thesis2lib.canvas_records import submission_has_rubric_grades

    return submission_has_rubric_grades(submission)


def choose_final_attempt(
    first_submission: dict | None,
    second_submission: dict | None,
) -> str | None:
    """Latest submission slot with an uploaded, graded thesis (for library delivery)."""
    if has_real_submission(second_submission) and submission_is_graded(second_submission):
        return "Second"
    if has_real_submission(first_submission) and submission_is_graded(first_submission):
        return "First"
    return None


def choose_thesis_attachment_attempt(
    first_submission: dict | None,
    second_submission: dict | None,
) -> str | None:
    """Same as choose_final_attempt: one graded thesis PDF from the latest graded slot."""
    return choose_final_attempt(first_submission, second_submission)
