"""Uploading a video to YouTube: resumable upload, metadata and thumbnail.

This module only talks to YouTube. It knows nothing about our database;
worker.py decides what to upload and what to do when something fails.

The resumable upload protocol (three kinds of request):

    1. start_session()    POST the title/description/tags/privacy as JSON.
                          YouTube answers with a session address (Location header).
    2. send_chunk()       PUT the file in pieces (8 MB) to that address,
                          each with a Content-Range header.
                          YouTube answers 308 "send me more" until the last
                          piece, then 200 with the new video's JSON.
    3. query_progress()   PUT an empty body with "Content-Range: bytes */TOTAL".
                          YouTube says how many bytes it already has. This is
                          how an interrupted upload continues where it stopped.

Every network call goes through _request(), so the tests replace that one
function with a fake YouTube and never touch the internet.

Every failure is raised as PublishError. It carries `retryable`: True means
"try again later" (network trouble, 5xx, quota); False means "retrying will
not help" (YouTube rejected the video, the title is invalid, no permission).

Quota note: videos.insert costs 1600 units and thumbnails.set costs 50 units.
The default quota is 10,000 units a day, so about six uploads a day.

Unverified-project note: videos uploaded through an API project that has not
passed Google's audit are forced to 'private' by YouTube, whatever privacy
we ask for. See docs/WORKER.md.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
GOOGLE_UPLOAD_PREFIX = "https://www.googleapis.com/"

# YouTube wants every chunk except the last to be a multiple of 256 KiB.
CHUNK_UNIT = 256 * 1024
CHUNK_SIZE = 8 * 1024 * 1024  # 32 x 256 KiB

CHUNK_TRIES = 3  # tries per chunk before the whole attempt is given up
CHUNK_BACKOFF_SECONDS = (2, 5)  # waits between those tries
SESSION_RESTARTS = 1  # how often an expired session may be replaced in one attempt

# A chunk is 8 MB, so allow a long time to write it on a slow connection.
REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

# 22 = "People & Blogs". Change it if your channel is mostly another category.
DEFAULT_CATEGORY_ID = "22"

# Hours until YouTube's daily quota resets are not worth computing:
# retry after a fixed wait instead.
QUOTA_RETRY_MINUTES = 180

_QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}


class PublishError(Exception):
    """A failed upload. The message is safe to show to the user."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        retry_in_minutes: int | None = None,
        code: str = "",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.retry_in_minutes = retry_in_minutes  # overrides the normal back-off
        self.code = code  # YouTube's reason, e.g. "quotaExceeded"


class SessionExpired(Exception):
    """YouTube forgot the upload session (404/410). Start a new one."""


def _sleep(seconds: float) -> None:
    """Wait. A separate function so tests can skip the waiting."""
    time.sleep(seconds)


# ------------------------------------------------------------ HTTP layer


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """The only function that touches the network."""
    try:
        return httpx.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise PublishError(
            "Could not reach YouTube (network problem).", retryable=True
        ) from exc


def _auth(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _error_from(response: httpx.Response) -> PublishError:
    """Turn an error response into a PublishError and decide if it is retryable."""
    status = response.status_code
    reason, message = "", ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            message = str(error.get("message", ""))
            details = error.get("errors") or [{}]
            reason = str(details[0].get("reason", ""))
    except (ValueError, AttributeError, IndexError, TypeError):
        pass
    message = (message or f"HTTP {status}")[:300]

    if reason in _QUOTA_REASONS:
        return PublishError(
            "YouTube's daily upload quota is used up. "
            f"Will try again in {QUOTA_RETRY_MINUTES // 60} hours.",
            retryable=True,
            retry_in_minutes=QUOTA_RETRY_MINUTES,
            code=reason,
        )
    if status in (401, 408, 429) or status >= 500:
        return PublishError(
            f"YouTube had a temporary problem ({status}).", retryable=True, code=reason
        )
    return PublishError(
        f"YouTube refused the request ({status}): {message}",
        retryable=False,
        code=reason,
    )


def _is_google_url(url: str) -> bool:
    """Never send our access token anywhere except to Google."""
    return isinstance(url, str) and url.startswith(GOOGLE_UPLOAD_PREFIX)


def _offset_from(response: httpx.Response) -> int:
    """How many bytes YouTube has, from a 308 reply's 'Range: bytes=0-N' header."""
    header = response.headers.get("Range", "")
    if "-" not in header:
        return 0  # nothing received yet
    try:
        return int(header.rsplit("-", 1)[1]) + 1
    except ValueError:
        return 0


# --------------------------------------------------- the three requests


def build_metadata(
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
    category_id: str = DEFAULT_CATEGORY_ID,
) -> dict:
    """The JSON body for videos.insert."""
    snippet: dict = {
        "title": title,
        "description": description,
        "categoryId": category_id,
    }
    if tags:
        snippet["tags"] = tags
    return {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy_status,
            # YouTube requires this answer. Product promos are not made for kids.
            "selfDeclaredMadeForKids": False,
        },
    }


def start_session(
    access_token: str, *, size: int, content_type: str, metadata: dict
) -> str:
    """Ask YouTube for a resumable upload address."""
    response = _request(
        "POST",
        UPLOAD_INIT_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            **_auth(access_token),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": content_type,
        },
        content=json.dumps(metadata).encode("utf-8"),
    )
    if response.status_code != 200:
        raise _error_from(response)
    session_url = response.headers.get("Location", "")
    if not _is_google_url(session_url):
        raise PublishError("YouTube did not return a valid upload address.", retryable=True)
    return session_url


def _read_upload_reply(response: httpx.Response, total: int) -> tuple[int, dict | None]:
    """(bytes YouTube has now, finished video JSON or None)."""
    status = response.status_code
    if status in (200, 201):
        try:
            return total, response.json()
        except ValueError as exc:
            raise PublishError(
                "YouTube finished the upload but sent an unreadable answer.",
                retryable=False,
            ) from exc
    if status == 308:
        return _offset_from(response), None
    if status in (404, 410):
        raise SessionExpired()
    raise _error_from(response)


def query_progress(
    session_url: str, access_token: str, total: int
) -> tuple[int, dict | None]:
    """Ask YouTube how much of the file it already has."""
    response = _request(
        "PUT",
        session_url,
        headers={**_auth(access_token), "Content-Range": f"bytes */{total}"},
        content=b"",
    )
    return _read_upload_reply(response, total)


def send_chunk(
    session_url: str, access_token: str, data: bytes, start: int, total: int
) -> tuple[int, dict | None]:
    """Upload bytes [start, start + len(data)) of the file."""
    end = start + len(data) - 1
    response = _request(
        "PUT",
        session_url,
        headers={
            **_auth(access_token),
            "Content-Range": f"bytes {start}-{end}/{total}",
        },
        content=data,
    )
    return _read_upload_reply(response, total)


def _send_with_retries(
    session_url: str, access_token: str, data: bytes, start: int, total: int
) -> tuple[int, dict | None]:
    """send_chunk() with a few quick retries for network blips.

    Before each retry we ask YouTube how much it really has, so a chunk that
    half-arrived is not sent twice from the wrong place. When the tries run
    out, the error is raised and the worker retries the whole post later;
    the saved session address lets that retry continue from the same byte.
    """
    last_error: PublishError | None = None
    for attempt in range(CHUNK_TRIES):
        if attempt:
            _sleep(CHUNK_BACKOFF_SECONDS[min(attempt - 1, len(CHUNK_BACKOFF_SECONDS) - 1)])
            try:
                have, resource = query_progress(session_url, access_token, total)
            except PublishError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
                continue
            if resource is not None or have != start:
                return have, resource  # the caller re-reads from `have`
        try:
            have, resource = send_chunk(session_url, access_token, data, start, total)
        except PublishError as exc:
            if not exc.retryable:
                raise
            last_error = exc
            continue
        if resource is None and have <= start:
            last_error = PublishError("YouTube accepted no data.", retryable=True)
            continue
        return have, resource
    raise last_error or PublishError("The upload failed.", retryable=True)


def upload_video(
    file_path: str | Path,
    access_token: str,
    metadata: dict,
    content_type: str,
    *,
    session_url: str | None = None,
    on_session=None,
    on_progress=None,
) -> str:
    """Upload a whole video file and return the YouTube video id.

    session_url   an address saved by an earlier attempt: continue that upload.
    on_session    called with a NEW session address (save it for a later retry).
    on_progress   called with (bytes_sent, total) after every chunk.
    """
    path = Path(file_path)
    total = path.stat().st_size
    if total == 0:
        raise PublishError("The video file is empty.", retryable=False)

    if session_url and not _is_google_url(session_url):
        session_url = None  # a saved address we do not trust: start over

    restarts = 0
    resource: dict | None = None
    offset = 0
    with path.open("rb") as handle:
        while resource is None:
            try:
                if session_url is None:
                    session_url = start_session(
                        access_token, size=total, content_type=content_type, metadata=metadata
                    )
                    offset = 0
                    if on_session:
                        on_session(session_url)
                else:
                    # A saved or continuing session: find out where YouTube is.
                    offset, resource = query_progress(session_url, access_token, total)
                    if resource is not None:
                        break

                while resource is None:
                    handle.seek(offset)
                    data = handle.read(CHUNK_SIZE)
                    if not data:
                        raise PublishError(
                            "YouTube has the whole file but has not confirmed it.",
                            retryable=True,
                        )
                    offset, resource = _send_with_retries(
                        session_url, access_token, data, offset, total
                    )
                    if on_progress:
                        on_progress(min(offset, total), total)
            except SessionExpired:
                # YouTube forgot the session (sessions last about a week).
                if restarts >= SESSION_RESTARTS:
                    raise PublishError(
                        "The upload session kept expiring.", retryable=True
                    ) from None
                restarts += 1
                session_url, offset, resource = None, 0, None
                if on_session:
                    on_session(None)  # forget the dead address

    video_id = str(resource.get("id", "")) if isinstance(resource, dict) else ""
    if not video_id:
        # Retrying could publish the video twice, so do not retry.
        raise PublishError(
            "YouTube finished the upload but did not return a video id. "
            "Check your channel before scheduling this video again.",
            retryable=False,
        )
    return video_id


def set_thumbnail(
    access_token: str, video_id: str, data: bytes, content_type: str
) -> None:
    """Attach a custom thumbnail. Needs a phone-verified YouTube channel."""
    response = _request(
        "POST",
        THUMBNAIL_URL,
        params={"videoId": video_id, "uploadType": "media"},
        headers={**_auth(access_token), "Content-Type": content_type},
        content=data,
    )
    if response.status_code != 200:
        raise _error_from(response)
