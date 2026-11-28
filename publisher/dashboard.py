"""Dashboard (Phase 8) and YouTube Stats Feedback Loop (Phase 10).

Routes:
    GET  /dashboard                        overview counts, upcoming & recent schedules,
                                           failed jobs with one-click Retry, and
                                           published video performance metrics
    POST /schedules/{id}/retry             reset a 'failed' schedule back to 'scheduled'
    POST /dashboard/sync-stats             pull fresh views/likes/comments from YouTube
                                           into the `video_stats` table
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from publisher.auth import require_user
from publisher.db import get_db
from publisher.models import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    STATUSES,
    Product,
    Schedule,
    User,
    Video,
    VideoStat,
    YouTubeAccount,
    utcnow,
)
from publisher.templating import templates
from publisher.worker import sync_published_stats

router = APIRouter()

_FLASH = "dashboard_message"


def _flash(request: Request, kind: str, text: str) -> None:
    request.session[_FLASH] = {"kind": kind, "text": text}


def _latest_stats_map(db: Session, schedule_ids: list[int]) -> dict[int, VideoStat]:
    """Return the most recent VideoStat row per schedule_id."""
    if not schedule_ids:
        return {}
    rows = list(
        db.scalars(
            select(VideoStat)
            .where(VideoStat.schedule_id.in_(schedule_ids))
            .order_by(VideoStat.captured_at.desc(), VideoStat.id.desc())
        ).all()
    )
    latest: dict[int, VideoStat] = {}
    for row in rows:
        if row.schedule_id not in latest:
            latest[row.schedule_id] = row
    return latest


@router.get("/dashboard")
def dashboard_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    status_counts = dict(
        db.execute(
            select(Schedule.status, func.count())
            .where(Schedule.user_id == user.id)
            .group_by(Schedule.status)
        ).all()
    )
    product_count = db.scalar(
        select(func.count()).select_from(Product).where(Product.user_id == user.id)
    ) or 0
    video_count = db.scalar(
        select(func.count()).select_from(Video).where(Video.user_id == user.id)
    ) or 0
    channel_count = db.scalar(
        select(func.count())
        .select_from(YouTubeAccount)
        .where(YouTubeAccount.user_id == user.id)
    ) or 0

    upcoming = list(
        db.scalars(
            select(Schedule)
            .where(
                Schedule.user_id == user.id,
                Schedule.status.in_((STATUS_SCHEDULED, STATUS_PROCESSING)),
            )
            .order_by(Schedule.scheduled_at_utc, Schedule.id)
            .limit(15)
        ).all()
    )

    failed = list(
        db.scalars(
            select(Schedule)
            .where(Schedule.user_id == user.id, Schedule.status == STATUS_FAILED)
            .order_by(Schedule.scheduled_at_utc.desc(), Schedule.id.desc())
            .limit(15)
        ).all()
    )

    published = list(
        db.scalars(
            select(Schedule)
            .where(Schedule.user_id == user.id, Schedule.status == STATUS_PUBLISHED)
            .order_by(Schedule.published_at.desc(), Schedule.id.desc())
            .limit(25)
        ).all()
    )

    stats_by_schedule = _latest_stats_map(db, [s.id for s in published])

    # Aggregate totals and A/B variant comparison (Phase 10 feedback loop)
    total_views = sum(st.views for st in stats_by_schedule.values())
    total_likes = sum(st.likes for st in stats_by_schedule.values())
    total_comments = sum(st.comments for st in stats_by_schedule.values())

    ab_summary: dict[str, dict] = {}
    for sch in published:
        label = (sch.variant_label or "").strip()
        if not label:
            continue
        bucket = ab_summary.setdefault(
            label, {"posts": 0, "views": 0, "likes": 0, "comments": 0}
        )
        bucket["posts"] += 1
        st = stats_by_schedule.get(sch.id)
        if st:
            bucket["views"] += st.views
            bucket["likes"] += st.likes
            bucket["comments"] += st.comments

    for bucket in ab_summary.values():
        v = bucket["views"]
        bucket["like_rate_pct"] = round((bucket["likes"] / v) * 100, 2) if v > 0 else 0.0

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "counts": {s: status_counts.get(s, 0) for s in STATUSES},
            "product_count": product_count,
            "video_count": video_count,
            "channel_count": channel_count,
            "upcoming": upcoming,
            "failed": failed,
            "published": published,
            "stats_by_schedule": stats_by_schedule,
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "ab_summary": ab_summary,
            "message": request.session.pop(_FLASH, None),
        },
    )


@router.post("/schedules/{schedule_id}/retry")
def schedule_retry(
    request: Request,
    schedule_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Scheduled post not found")

    if schedule.status != STATUS_FAILED:
        _flash(request, "error", "Only failed posts can be retried.")
        return RedirectResponse("/dashboard", status_code=303)

    schedule.status = STATUS_SCHEDULED
    schedule.attempts = 0
    schedule.next_retry_at = None
    schedule.error_message = None
    schedule.scheduled_at_utc = utcnow()
    db.commit()

    _flash(request, "ok", "Post queued for immediate retry on the next worker run.")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/dashboard/sync-stats")
def dashboard_sync_stats(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    count = sync_published_stats(db, user_id=user.id)
    if count:
        _flash(request, "ok", f"Synced YouTube statistics for {count} published video(s).")
    else:
        _flash(
            request,
            "ok",
            "No new statistics recorded (ensure videos are published and YouTube is connected).",
        )
    return RedirectResponse("/dashboard", status_code=303)
