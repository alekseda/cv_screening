"""
Glorri.az integration: pulls candidate applications from a company's Glorri
account into this tool, so a recruiter doesn't have to download resumes from
Glorri and re-upload them here by hand.

Glorri has no per-job "give me new applicants" endpoint. The only
integration API is a company-WIDE export: kick off an async export job,
poll it until it's ready, then download one JSON file containing every
active job posting and every candidate across every pipeline stage.
Filtering that down to one vacancy's fresh (APPLIED-stage) applicants
happens on our side - see sync_glorri_applicants in routes/candidates.py.

Like the Anthropic/Voyage clients elsewhere in this codebase, these are
synchronous/blocking calls - callers from async route handlers should wrap
them in anyio.to_thread.run_sync.
"""
import time

import httpx

from app.core.config import settings

DEFAULT_TIMEOUT_SECONDS = 30.0
# Glorri returns status="started" while a fresh export is being generated
# (it reuses a cached export from the last 4 hours instead, when one exists,
# which is normally near-instant) - poll until "completed" or give up.
EXPORT_POLL_INTERVAL_SECONDS = 3.0
EXPORT_POLL_TIMEOUT_SECONDS = 120.0
RESUME_DOWNLOAD_TIMEOUT_SECONDS = 30.0
# Matches candidates.py's own per-upload limit, so a Glorri-sourced resume
# can't bypass the size ceiling a manually-uploaded one is held to.
MAX_RESUME_DOWNLOAD_BYTES = 15 * 1024 * 1024

_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


class GlorriError(Exception):
    pass


def _headers() -> dict:
    if not settings.GLORRI_API_TOKEN:
        raise GlorriError("GLORRI_API_TOKEN is not configured")
    return {"Authorization": f"Bearer {settings.GLORRI_API_TOKEN}", "accept-language": "az"}


def start_export() -> dict:
    """Starts (or reuses, per Glorri's own recent-export caching) a
    company-wide export job and polls until it's done.

    Returns the response's "data" object, which includes `downloadUrl` once
    `status` is "completed". Raises GlorriError on any request failure,
    unexpected status value, or a poll timeout - callers should surface this
    to the recruiter rather than treat it as "no new candidates".
    """
    url = f"{settings.GLORRI_API_BASE_URL}/integration-service/operations/export/report/start"
    deadline = time.monotonic() + EXPORT_POLL_TIMEOUT_SECONDS

    with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
        while True:
            try:
                response = client.post(url, headers=_headers())
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as e:
                raise GlorriError(f"Glorri export request failed: {e}") from e

            if not payload.get("ok"):
                raise GlorriError(f"Glorri export request returned ok=false: {payload}")

            data = payload.get("data") or {}
            status_value = data.get("status")
            if status_value == "completed":
                if not data.get("downloadUrl"):
                    raise GlorriError("Glorri export completed but returned no downloadUrl")
                return data
            if status_value not in ("started", "pending"):
                raise GlorriError(f"Glorri export returned unexpected status {status_value!r}")

            if time.monotonic() >= deadline:
                raise GlorriError(
                    f"Glorri export did not complete within {EXPORT_POLL_TIMEOUT_SECONDS:.0f}s "
                    f"(last status: {status_value!r})"
                )
            time.sleep(EXPORT_POLL_INTERVAL_SECONDS)


def download_export(download_url: str) -> dict:
    """Downloads and parses the completed export's JSON body (the `jobs` /
    `blacklistCandidates` structure)."""
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = client.get(download_url, headers=_headers())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise GlorriError(f"Failed to download Glorri export: {e}") from e
    except ValueError as e:  # invalid JSON
        raise GlorriError(f"Glorri export was not valid JSON: {e}") from e


def download_resume(resume_url: str) -> tuple[bytes, str]:
    """Downloads one candidate's résumé file. Returns (content, filename).

    `resumeUrl` points wherever Glorri stored the upload - not necessarily a
    Glorri API host, so no Authorization header is sent (these have been
    plain/presigned links in practice). The filename is taken from the URL's
    last path segment; if that has no recognisable extension, falls back to
    the response's Content-Type.
    """
    try:
        with httpx.Client(timeout=RESUME_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            with client.stream("GET", resume_url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESUME_DOWNLOAD_BYTES:
                        raise GlorriError(
                            f"Resume exceeds the {MAX_RESUME_DOWNLOAD_BYTES // (1024 * 1024)}MB limit"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
    except httpx.HTTPError as e:
        raise GlorriError(f"Failed to download resume from {resume_url}: {e}") from e

    filename = resume_url.rsplit("/", 1)[-1].split("?")[0] or "resume"
    if "." not in filename:
        ext = _CONTENT_TYPE_EXTENSIONS.get(content_type)
        filename = f"{filename}.{ext}" if ext else filename

    return content, filename
