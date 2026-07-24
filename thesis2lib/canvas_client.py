"""Canvas REST API client."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterator

import requests

from thesis2lib.constants import ENROLLMENT_INCLUDES, STUDENT_ENROLLMENT_TYPE, SUBMISSION_INCLUDES
from thesis2lib.progress import ConsoleProgress
from thesis2lib.utils import clean_text


class CanvasAPIError(Exception):
    """Raised when the Canvas API returns an unrecoverable error."""


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _request(
        self,
        method: str,
        path: str,
        *,
        full_url: str | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        url = full_url or f"{self.base_url}{path}"
        delay = 1.0
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                last_error = exc
                logging.warning(
                    "Canvas request timed out (attempt %s/%s): %s",
                    attempt,
                    self.max_retries,
                    path,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            except requests.RequestException as exc:
                raise CanvasAPIError(
                    f"Network error while calling Canvas: {exc}"
                ) from exc

            if response.status_code == 401:
                raise CanvasAPIError("Canvas authentication failed (401).")
            if response.status_code == 403:
                raise CanvasAPIError("Canvas permission denied (403).")
            if response.status_code == 404:
                raise CanvasAPIError(f"Canvas resource not found (404): {path}")
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", delay))
                logging.warning(
                    "Canvas rate limit hit; retrying in %s seconds.", retry_after
                )
                time.sleep(retry_after)
                continue
            if 500 <= response.status_code < 600:
                logging.warning(
                    "Canvas server error %s (attempt %s/%s): %s",
                    response.status_code,
                    attempt,
                    self.max_retries,
                    path,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            return response

        if last_error is not None:
            raise CanvasAPIError(f"Canvas request timed out: {path}") from last_error
        raise CanvasAPIError(f"Canvas request failed after retries: {path}")

    def download_file(self, url: str, destination: Path, *, overwrite: bool = False) -> Path:
        if not url:
            raise CanvasAPIError("Canvas attachment download URL is missing.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            return destination

        response = self._request("GET", path="", full_url=url, stream=True)
        try:
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
        except OSError as exc:
            raise CanvasAPIError(
                f"Could not write downloaded Canvas file to {destination}"
            ) from exc

        if destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            raise CanvasAPIError("Canvas attachment download returned an empty file.")

        return destination

    def paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict]:
        query = dict(params or {})
        query.setdefault("per_page", 100)
        next_url: str | None = None

        while True:
            if next_url is None:
                response = self._request("GET", path, params=query)
            else:
                response = self._request("GET", path, full_url=next_url)

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise CanvasAPIError("Canvas returned invalid JSON.") from exc

            if not isinstance(payload, list):
                raise CanvasAPIError("Unexpected Canvas pagination payload.")

            for item in payload:
                yield item

            next_url = self._parse_next_link(response.headers.get("Link", ""))
            if not next_url:
                break

    @staticmethod
    def _parse_next_link(link_header: str) -> str | None:
        if not link_header:
            return None
        for part in link_header.split(","):
            section = part.strip()
            if 'rel="next"' in section:
                start = section.find("<")
                end = section.find(">")
                if start != -1 and end != -1:
                    return section[start + 1 : end]
        return None

    def get_assignments(self, course_id: int) -> list[dict]:
        items = list(
            self.paginate(
                f"/api/v1/courses/{course_id}/assignments",
                params={"per_page": 100},
            )
        )
        logging.info("Retrieved %s course assignment(s).", len(items))
        return items

    def get_all_assignment_submissions(
        self,
        course_id: int,
        assignments: list[dict],
        *,
        progress: ConsoleProgress | None = None,
    ) -> list[dict]:
        """Fetch submissions per assignment (works with teacher tokens).

        The bulk ``/students/submissions`` endpoint requires grade-export
        permissions and returns 403 for typical course tokens.
        """
        submissions: list[dict] = []
        for assignment in assignments:
            assignment_id = assignment.get("id")
            if assignment_id is None:
                continue
            submissions.extend(
                self.get_submissions(
                    course_id,
                    int(assignment_id),
                    log_retrieval=progress is None,
                )
            )
            if progress:
                name = clean_text(assignment.get("name")) or str(assignment_id)
                progress.advance(name[:36])
        if progress is None:
            logging.info(
                "Retrieved %s gradebook submission record(s) across %s assignment(s).",
                len(submissions),
                len(assignments),
            )
        return submissions

    def get_enrollments(self, course_id: int) -> list[dict]:
        items = list(
            self.paginate(
                f"/api/v1/courses/{course_id}/enrollments",
                params={
                    "type[]": [STUDENT_ENROLLMENT_TYPE],
                    "state[]": ["active", "invited", "completed"],
                    "include[]": list(ENROLLMENT_INCLUDES),
                },
            )
        )
        logging.info("Retrieved %s course enrollment record(s).", len(items))
        return items

    def get_submissions(
        self,
        course_id: int,
        assignment_id: int,
        *,
        log_retrieval: bool = True,
    ) -> list[dict]:
        items = list(
            self.paginate(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
                params={"include[]": list(SUBMISSION_INCLUDES)},
            )
        )
        if log_retrieval:
            logging.info(
                "Retrieved %s submission record(s) for assignment %s.",
                len(items),
                assignment_id,
            )
        return items

    def validate_course(self, course_id: int) -> None:
        response = self._request("GET", f"/api/v1/courses/{course_id}")
        try:
            response.json()
        except json.JSONDecodeError as exc:
            raise CanvasAPIError("Canvas returned invalid JSON for course.") from exc

    def validate_assignment(self, course_id: int, assignment_id: int) -> None:
        response = self._request(
            "GET",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
        )
        try:
            response.json()
        except json.JSONDecodeError as exc:
            raise CanvasAPIError(
                f"Canvas returned invalid JSON for assignment {assignment_id}."
            ) from exc
