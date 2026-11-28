"""The publisher worker (Phase 7) and the YouTube statistics sync (Phase 10).

Run it from the terminal:

    python -m publisher.worker --once        one pass, then exit
    python -m publisher.worker               keep running, one pass every minute
    python -m publisher.worker --once --max-jobs 1

or let the host call the cron URL (see cron.py).

One pass does this:

    1. recover_stale()   a post stuck in 'processing' because an earlier worker
                         died goes back to 'scheduled' (or fails if it has used
                         up its attempts)
    2. claim_next()      take ONE due post: status 'scheduled' -> 'processing'.
                         It is a single UPDATE ... WHERE status = 'scheduled',
                         so two workers can never get the same post.
    3. process_claimed() upload it (resumable, in chunks), set the thumbnail,
                         mark it 'published'. When something goes wrong:
                           * permanent problem (YouTube refused the video, the
                             channel access was revoked, the file is gone)
                                 -> status 'failed' at once
                           * temporary problem (network, 5xx, quota)
                                 -> back to 'scheduled' with next_retry_at,
                                    waiting 2, 10, 30, then 120 minutes
                                    (a quota error waits 3 hours),
                                    and 'failed' after MAX_ATTEMPTS attempts
    4. repeat from 2 until nothing is due (or --max-jobs is reached)

Why a stuck worker cannot publish a video twice:
    * The saved upload session address (schedules.upload_session_url) lets a
      retry continue where the last attempt stopped.
    * The YouTube video id is saved the moment the upload finishes
      (schedules.youtube_video_id), so a crash afterwards never uploads again.
    * claimed_at is refreshed after every uploaded chunk, so a long but healthy
      upload is never mistaken for a dead worker.

Messages saved in schedules.error_message are written for the user: they never
contain tokens, upload addresses or Python error text (those go to the log).
"""

from __future__ import annotations

import argparse
import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from publisher import google_oauth, youtube_api
from publisher.db import SessionLocal, init_db
from publisher.google_oauth import YouTubeError
from publisher.models import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    Schedule,
    VideoStat,
    YouTubeAccount,
    utcnow,
)
from publisher.storage import content_type_for_key, get_storage
from publisher.youtube_api import PublishError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Minutes to wait after attempt 1, 2, 3, 4. (Attempt 5 is the last one.)
RETRY_WAIT_MINUTES = (2, 10, 30, 120)
# A 'processing' post whose claimed_at is older than this means the worker died.
# claimed_at is refreshed after every chunk, so a healthy upload never gets here.
STALE_AFTER_MINUTES = 15
POLL_SECONDS = 60  # pause between passes when the worker runs without --once
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v="
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

MAX_MESSAGE_LENGTH = 500


@dataclass
class RunSummary:
    """What one worker pass did."""

    recovered: int = 0  # abandoned posts handed back (or failed)
    claimed: int = 0
    processed: int = 0
    published: int = 0
    retried: int = 0  # failed this time, will try again later
    failed: int = 0  # failed for good

    def __str__(self) -> str:
        return (
            f"claimed={self.claimed} processed={self.processed} "
            f"published={self.published} retried={self.retried} "
            f"failed={self.failed} recovered={self.recovered}"
        )

    def __getitem__(self, name: str) -> int:
        """summary["published"] works as well as summary.published."""
        return getattr(self, name)


def access_token_for(account: YouTubeAccount) -> str:
    """A fresh Google access token for a channel. Kept as a small wrapper so
    tests can replace it here or in google_oauth."""
    return google_oauth.access_token_for(account)


# ------------------------------------------------------------- small helpers


def _set(db: Session, schedule_id: int, **values) -> None:
    """Change columns of one schedule row and save right away."""
    db.execute(update(Schedule).where(Schedule.id == schedule_id).values(**values))
    db.commit()


def _clip(message: str) -> str:
    return message[:MAX_MESSAGE_LENGTH]


# ---------------------------------------------------------------- claiming


def claim_next(db: Session, now: datetime | None = None) -> int | None:
    """Claim the oldest due post and return its id, or None if nothing is due.

    'Due' means: status 'scheduled', its time has come, and any retry wait is over.
    The claim is one conditional UPDATE: if another worker got there first the
    update changes zero rows and we try the next candidate.
    """
    now = now or utcnow()
    candidates = db.scalars(
        select(Schedule.id)
        .where(
            Schedule.status == STATUS_SCHEDULED,
            Schedule.scheduled_at_utc <= now,
            (Schedule.next_retry_at.is_(None)) | (Schedule.next_retry_at <= now),
        )
        .order_by(Schedule.scheduled_at_utc, Schedule.id)
        .limit(10)
    ).all()
    db.commit()  # end the read transaction before we start writing

    for schedule_id in candidates:
        result = db.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id, Schedule.status == STATUS_SCHEDULED)
            .values(
                status=STATUS_PROCESSING,
                attempts=Schedule.attempts + 1,
                claimed_at=now,
            )
        )
        db.commit()
        if result.rowcount == 1:
            return schedule_id
    return None


def recover_stale(db: Session, now: datetime | None = None) -> int:
    """Deal with posts abandoned by a worker that died. Returns how many.

    A post counts as abandoned when it is 'processing' and its claimed_at is
    older than STALE_AFTER_MINUTES. It goes back to 'scheduled' (keeping its
    saved upload session so the upload can continue), unless it has no
    attempts left; then it fails.
    """
    now = now or utcnow()
    cutoff = now - timedelta(minutes=STALE_AFTER_MINUTES)
    rows = db.execute(
        select(Schedule.id, Schedule.attempts).where(
            Schedule.status == STATUS_PROCESSING,
            (Schedule.claimed_at.is_(None)) | (Schedule.claimed_at <= cutoff),
        )
    ).all()
    db.commit()

    recovered = 0
    for schedule_id, attempts in rows:
        if attempts >= MAX_ATTEMPTS:
            values = dict(
                status=STATUS_FAILED,
                claimed_at=None,
                next_retry_at=None,
                error_message=(
                    "The worker stopped while publishing this post and it has "
                    "no attempts left."
                ),
            )
        else:
            values = dict(
                status=STATUS_SCHEDULED,
                claimed_at=None,
                error_message=(
                    "The worker stopped while publishing this post. Trying again."
                ),
            )
        # Still conditional: a live worker may have refreshed claimed_at meanwhile.
        result = db.execute(
            update(Schedule)
            .where(
                Schedule.id == schedule_id,
                Schedule.status == STATUS_PROCESSING,
                (Schedule.claimed_at.is_(None)) | (Schedule.claimed_at <= cutoff),
            )
            .values(**values)
        )
        db.commit()
        recovered += result.rowcount or 0
    return recovered


def _hand_back(db: Session, schedule_id: int) -> None:
    """The worker was stopped (Ctrl+C) in the middle of a post: put it back as if
    this attempt never happened. The saved upload session is kept."""
    db.rollback()
    db.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id, Schedule.status == STATUS_PROCESSING)
        .values(
            status=STATUS_SCHEDULED,
            attempts=Schedule.attempts - 1,
            claimed_at=None,
        )
    )
    db.commit()


# --------------------------------------------------------- doing one post


def _error_for_token_problem(exc: YouTubeError) -> PublishError:
    if exc.code == "invalid_grant":
        return PublishError(
            "YouTube no longer lets this app post to the channel (access was "
            "revoked or expired). Reconnect the channel on the YouTube page, "
            "then retry the post.",
            retryable=False,
            code="invalid_grant",
        )
    # Google was unreachable or answered with a hiccup: worth another try.
    return PublishError(str(exc) or "Could not get YouTube access.", retryable=True)


def _get_token(account: YouTubeAccount) -> str:
    try:
        return access_token_for(account)
    except YouTubeError as exc:
        raise _error_for_token_problem(exc) from exc
    except ValueError as exc:  # the saved refresh token cannot be decrypted
        log.error("Could not read the saved YouTube login for channel %s", account.channel_id)
        raise PublishError(
            "The saved YouTube login could not be read. Reconnect the channel "
            "on the YouTube page, then retry the post.",
            retryable=False,
        ) from exc


def _split_tags(raw: str) -> list[str]:
    return [tag.strip() for tag in (raw or "").split(",") if tag.strip()]


def _upload(db: Session, schedule_id: int, storage, token: str, video, session_url) -> str:
    """Copy the video out of storage and upload it. Returns the YouTube video id."""
    metadata = youtube_api.build_metadata(
        title=(video["title"] or "Untitled video")[:100],
        description=video["description"] or "",
        tags=_split_tags(video["tags"]),
        privacy_status=video["privacy_status"] or "private",
    )
    mime_type = content_type_for_key(video["storage_key"]) or "video/mp4"
    suffix = Path(video["storage_key"]).suffix or ".mp4"

    def remember_session(url: str | None) -> None:
        _set(db, schedule_id, upload_session_url=url)

    def still_alive(_sent: int, _total: int) -> None:
        # A heartbeat, so recover_stale() knows this upload is healthy.
        _set(db, schedule_id, claimed_at=utcnow())

    with tempfile.TemporaryDirectory(prefix="publisher-") as folder:
        local = Path(folder) / f"video{suffix}"
        try:
            storage.download_to_path(video["storage_key"], local)
        except FileNotFoundError as exc:
            raise PublishError(
                "The video file is missing from storage. Upload the video again.",
                retryable=False,
            ) from exc
        except Exception as exc:  # bucket unreachable, disk full, ...
            log.error("Could not read %s from storage: %r", video["storage_key"], exc)
            raise PublishError(
                "Could not read the video file from storage.", retryable=True
            ) from exc

        return youtube_api.upload_video(
            local,
            token,
            metadata,
            mime_type,
            session_url=session_url,
            on_session=remember_session,
            on_progress=still_alive,
        )


def _set_thumbnail(storage, token: str, youtube_video_id: str, thumbnail_key: str | None) -> str | None:
    """Attach the custom thumbnail. Never raises: the video is already public by
    now, so a failed thumbnail is reported but does not fail the post.
    Returns None on success, otherwise a short reason."""
    if not thumbnail_key:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="publisher-") as folder:
            local = Path(folder) / f"thumbnail{Path(thumbnail_key).suffix or '.jpg'}"
            storage.download_to_path(thumbnail_key, local)
            data = local.read_bytes()
        youtube_api.set_thumbnail(
            token,
            youtube_video_id,
            data,
            content_type_for_key(thumbnail_key) or "image/jpeg",
        )
    except FileNotFoundError:
        return "the thumbnail file is missing from storage."
    except PublishError as exc:
        return str(exc)
    except Exception:  # noqa: BLE001 - nothing here may stop the post being saved
        log.exception("Unexpected error while setting the thumbnail")
        return "an unexpected error occurred (see the server log)."
    return None


def _fail_or_retry(db: Session, schedule_id: int, attempts: int, error: PublishError) -> str:
    """Decide what a failed attempt means. Returns the post's new status."""
    message = str(error)
    if not error.retryable:
        _set(
            db, schedule_id,
            status=STATUS_FAILED, error_message=_clip(message),
            next_retry_at=None, claimed_at=None,
        )
        return STATUS_FAILED

    if attempts >= MAX_ATTEMPTS:
        _set(
            db, schedule_id,
            status=STATUS_FAILED,
            error_message=_clip(f"Gave up after {MAX_ATTEMPTS} attempts: {message}"),
            next_retry_at=None, claimed_at=None,
        )
        return STATUS_FAILED

    wait = error.retry_in_minutes or RETRY_WAIT_MINUTES[
        min(max(attempts, 1) - 1, len(RETRY_WAIT_MINUTES) - 1)
    ]
    _set(
        db, schedule_id,
        status=STATUS_SCHEDULED,
        next_retry_at=utcnow() + timedelta(minutes=wait),
        error_message=_clip(f"Attempt {attempts} of {MAX_ATTEMPTS} failed: {message}"),
        claimed_at=None,
    )
    return STATUS_SCHEDULED


def process_claimed(db: Session, schedule_id: int, storage=None) -> str:
    """Publish one post that claim_next() returned. Returns its new status."""
    storage = storage or get_storage()

    row = db.get(Schedule, schedule_id, populate_existing=True)  # fresh values, not cached ones
    if row is None:
        return "missing"
    video_row, account = row.video, row.youtube_account
    attempts = row.attempts
    session_url = row.upload_session_url
    youtube_video_id = row.youtube_video_id
    # Copy what we need into plain values, then end the transaction: the upload
    # can take many minutes and must not hold a database transaction open.
    video = {
        "storage_key": video_row.storage_key,
        "title": video_row.title,
        "description": video_row.description,
        "tags": video_row.tags,
        "privacy_status": video_row.privacy_status,
        "thumbnail_key": video_row.thumbnail_key,
    }
    db.commit()

    try:
        token = _get_token(account)
        if not youtube_video_id:
            youtube_video_id = _upload(db, schedule_id, storage, token, video, session_url)
            # Save the id at once: from here on this video must never be uploaded again.
            _set(db, schedule_id, youtube_video_id=youtube_video_id, upload_session_url=None)
        warning = _set_thumbnail(storage, token, youtube_video_id, video["thumbnail_key"])
    except PublishError as exc:
        return _fail_or_retry(db, schedule_id, attempts, exc)
    except (KeyboardInterrupt, SystemExit):
        _hand_back(db, schedule_id)
        raise
    except Exception:  # noqa: BLE001 - a bug must not leave the post stuck
        log.exception("Unexpected error while publishing schedule %s", schedule_id)
        return _fail_or_retry(
            db, schedule_id, attempts,
            PublishError(
                "An unexpected error occurred (details are in the server log).",
                retryable=True,
            ),
        )

    _set(
        db, schedule_id,
        status=STATUS_PUBLISHED,
        youtube_video_id=youtube_video_id,
        youtube_url=f"{YOUTUBE_WATCH_URL}{youtube_video_id}",
        published_at=utcnow(),
        error_message=_clip(f"Published, but the thumbnail was not set: {warning}") if warning else None,
        next_retry_at=None,
        claimed_at=None,
        upload_session_url=None,
    )
    return STATUS_PUBLISHED


# ------------------------------------------------------------- a full pass


def run_once(
    max_jobs: int | None = None,
    *,
    db: Session | None = None,
    storage=None,
) -> RunSummary:
    """One worker pass: recover abandoned posts, then publish what is due.

    max_jobs   stop after this many posts (None = everything that is due)
    db         use this session (tests); otherwise a new one is opened
    storage    use this storage (tests); otherwise the configured one
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()
    summary = RunSummary()
    try:
        summary.recovered = recover_stale(db)
        while max_jobs is None or summary.processed < max_jobs:
            schedule_id = claim_next(db)
            if schedule_id is None:
                break
            summary.claimed += 1
            outcome = process_claimed(db, schedule_id, storage=storage)
            summary.processed += 1
            if outcome == STATUS_PUBLISHED:
                summary.published += 1
            elif outcome == STATUS_SCHEDULED:
                summary.retried += 1
            elif outcome == STATUS_FAILED:
                summary.failed += 1
        return summary
    finally:
        if own_session:
            db.close()


# --------------------------------------------------- Phase 10: stats fetcher


def fetch_video_statistics(access_token: str, youtube_video_ids: list[str]) -> dict[str, dict]:
    """YouTube Data API `videos.list(part=statistics)` for up to 50 videos."""
    if not youtube_video_ids:
        return {}
    try:
        resp = httpx.get(
            YOUTUBE_VIDEOS_URL,
            params={"part": "statistics", "id": ",".join(youtube_video_ids[:50])},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise YouTubeError("Could not reach the YouTube statistics API.") from exc

    if resp.status_code >= 400:
        raise YouTubeError(f"YouTube statistics API returned {resp.status_code}.")

    out: dict[str, dict] = {}
    for item in resp.json().get("items") or []:
        video_id = item.get("id")
        stats = item.get("statistics") or {}
        if video_id:
            out[video_id] = {
                "views": int(stats.get("viewCount", 0) or 0),
                "likes": int(stats.get("likeCount", 0) or 0),
                "comments": int(stats.get("commentCount", 0) or 0),
            }
    return out


def sync_published_stats(db: Session, user_id: int | None = None) -> int:
    """Record the latest views / likes / comments of published videos as
    VideoStat rows. Returns how many rows were added."""
    query = select(Schedule).where(
        Schedule.status == STATUS_PUBLISHED,
        Schedule.youtube_video_id.is_not(None),
    )
    if user_id is not None:
        query = query.where(Schedule.user_id == user_id)
    published = list(db.scalars(query).all())
    if not published:
        return 0

    by_account: dict[int, list[Schedule]] = {}
    for schedule in published:
        by_account.setdefault(schedule.youtube_account_id, []).append(schedule)

    recorded = 0
    now = utcnow()
    for account_id, items in by_account.items():
        account = db.get(YouTubeAccount, account_id)
        if account is None:
            continue
        try:
            token = access_token_for(account)
        except (YouTubeError, ValueError):
            continue

        for start in range(0, len(items), 50):
            batch = items[start : start + 50]
            ids = [s.youtube_video_id for s in batch if s.youtube_video_id]
            try:
                stats_map = fetch_video_statistics(token, ids)
            except YouTubeError:
                continue
            for schedule in batch:
                metrics = stats_map.get(schedule.youtube_video_id or "")
                if metrics is None:
                    continue
                db.add(
                    VideoStat(
                        schedule_id=schedule.id,
                        captured_at=now,
                        views=metrics["views"],
                        likes=metrics["likes"],
                        comments=metrics["comments"],
                    )
                )
                recorded += 1
    db.commit()
    return recorded


# ------------------------------------------------------------ command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the posts that are due.")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--max-jobs", type=int, default=None, help="posts per pass")
    parser.add_argument(
        "--interval", type=int, default=POLL_SECONDS,
        help="seconds between passes when running without --once",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()  # also upgrades an older database file

    try:
        while True:
            summary = run_once(max_jobs=args.max_jobs)
            print(f"Worker finished: {summary}", flush=True)
            if args.once:
                return 0
            time.sleep(max(args.interval, 1))
    except KeyboardInterrupt:
        print("Worker stopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
