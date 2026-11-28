"""The cron URL: lets a web host (or a free ping service) run the worker.

Free hosts such as Render put a web app to sleep and cannot run a background
process, so an outside scheduler calls this URL every few minutes instead:

    POST https://YOUR-APP/cron/publish
    X-Cron-Token: <CRON_TOKEN>

The token can also be sent as "Authorization: Bearer <CRON_TOKEN>" or as
?token=<CRON_TOKEN> (for ping services that can only open a plain URL), and
GET works as well as POST. /cron/run is kept as another name for the same URL.

The URL answers at once and does the work in the background, because a video
upload takes far longer than a ping service waits:

    202 {"status": "started"}   a worker pass is now running
    202 {"status": "busy"}      the previous pass is still running; nothing started
    403                         missing or wrong token
    503                         CRON_TOKEN is not set, so the URL is switched off

Only one pass runs at a time inside this process (_run_lock). Several processes
are still safe: claiming a post is one atomic UPDATE in the database.

Once an hour the same pass also refreshes the YouTube statistics (Phase 10).
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query

from publisher import worker
from publisher.config import get_settings
from publisher.db import SessionLocal
from publisher.models import utcnow

log = logging.getLogger(__name__)

router = APIRouter()

STATS_SYNC_EVERY = timedelta(hours=1)

_run_lock = threading.Lock()  # held while a pass is running
_last_stats_sync: datetime | None = None


def _check_token(authorization: str, x_cron_token: str, token: str) -> None:
    expected = get_settings().CRON_TOKEN
    if not expected:
        raise HTTPException(status_code=503, detail="The cron URL is switched off (CRON_TOKEN is not set).")

    provided = x_cron_token.strip() or token.strip()
    if not provided and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid cron token.")


def _sync_stats_if_due() -> None:
    """Refresh YouTube statistics at most once per STATS_SYNC_EVERY."""
    global _last_stats_sync
    now = utcnow()
    if _last_stats_sync is not None and now - _last_stats_sync < STATS_SYNC_EVERY:
        return
    _last_stats_sync = now
    with SessionLocal() as db:
        added = worker.sync_published_stats(db)
    log.info("Statistics sync recorded %s snapshot(s).", added)


def _run_pass() -> None:
    """One background pass. Never raises, and always releases the lock."""
    try:
        summary = worker.run_once()
        log.info("Cron pass finished: %s", summary)
        _sync_stats_if_due()
    except Exception:  # noqa: BLE001 - a crash must not block the next ping
        log.exception("Cron pass crashed")
    finally:
        _run_lock.release()


@router.api_route("/cron/publish", methods=["GET", "POST"], status_code=202)
@router.api_route("/cron/run", methods=["GET", "POST"], status_code=202, include_in_schema=False)
def cron_publish(
    background: BackgroundTasks,
    authorization: str = Header(""),
    x_cron_token: str = Header(""),
    token: str = Query(""),
) -> dict:
    _check_token(authorization, x_cron_token, token)
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy"}
    background.add_task(_run_pass)
    return {"status": "started"}
