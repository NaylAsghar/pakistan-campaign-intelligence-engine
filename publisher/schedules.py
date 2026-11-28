"""Scheduling: choose a video, a channel and a date/time, then manage the plan.

HTML pages (login required; anonymous visitors go to /login):
    GET  /schedules                 my scheduled posts (?status=scheduled|published|...)
    GET  /schedules/new             create form (?video_id=3 pre-selects a video)
    POST /schedules                 create
    GET  /schedules/{id}/edit       edit form (only while status is 'scheduled')
    POST /schedules/{id}/edit       save
    POST /schedules/{id}/delete     delete (not while the worker is publishing it)

The user types the time in Asia/Karachi; we store it as UTC. This phase only
creates the plan. The background worker that publishes due posts is Phase 7:
it looks for rows whose status is 'scheduled' and scheduled_at_utc <= now.

A post that belongs to another user is always reported as 404.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from publisher.auth import require_user
from publisher.config import get_settings
from publisher.db import get_db
from publisher.models import (
    STATUS_PROCESSING,
    STATUS_SCHEDULED,
    STATUSES,
    Schedule,
    User,
    Video,
    VideoStat,
    YouTubeAccount,
    utcnow,
)
from publisher.templating import templates
from publisher.timeutil import (
    format_local,
    local_input_to_utc,
    to_input_value,
)

router = APIRouter()

MIN_LEAD_MINUTES = 5  # the worker needs a little time to notice a new post
MAX_DAYS_AHEAD = 365

_FLASH = "schedule_message"

# Statuses that mean "this video is still going to be published on this channel".
_ACTIVE_STATUSES = (STATUS_SCHEDULED, STATUS_PROCESSING)


# ---------------------------------------------------------------- helpers


def _page(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )


def _flash(request: Request, kind: str, text: str) -> None:
    """One message shown on the next /schedules page view."""
    request.session[_FLASH] = {"kind": kind, "text": text}


def _back_to_list() -> RedirectResponse:
    return RedirectResponse("/schedules", status_code=303)


def _owned_schedule(db: Session, user: User, schedule_id: int) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    return schedule


def _my_videos(db: Session, user: User) -> list[Video]:
    return list(
        db.scalars(
            select(Video).where(Video.user_id == user.id).order_by(Video.title, Video.id)
        ).all()
    )


def _my_accounts(db: Session, user: User) -> list[YouTubeAccount]:
    return list(
        db.scalars(
            select(YouTubeAccount)
            .where(YouTubeAccount.user_id == user.id)
            .order_by(YouTubeAccount.channel_title, YouTubeAccount.id)
        ).all()
    )


def _pick(db: Session, user: User, raw_video_id, raw_account_id) -> tuple[Video, YouTubeAccount]:
    """The user's own video and channel for the form values.
    Raises ValueError (message safe to show) when a value is missing or not theirs."""
    try:
        video = db.get(Video, int(raw_video_id))
    except (TypeError, ValueError):
        video = None
    if video is None or video.user_id != user.id:
        raise ValueError("Choose a video from the list.")
    try:
        account = db.get(YouTubeAccount, int(raw_account_id))
    except (TypeError, ValueError):
        account = None
    if account is None or account.user_id != user.id:
        raise ValueError("Choose a YouTube channel from the list.")
    return video, account


def validate_when(when_utc) -> str | None:
    """The problem with a publish time (given in UTC), or None if it is fine."""
    now = utcnow()
    if when_utc < now + timedelta(minutes=MIN_LEAD_MINUTES):
        return f"Choose a time at least {MIN_LEAD_MINUTES} minutes from now."
    if when_utc > now + timedelta(days=MAX_DAYS_AHEAD):
        return f"Choose a time within the next {MAX_DAYS_AHEAD} days."
    return None


def _already_planned(db: Session, video_id: int, account_id: int, skip_id: int | None) -> bool:
    """True if this video is already waiting to go out on this channel.
    Stops the same video being published twice by accident."""
    query = select(func.count()).select_from(Schedule).where(
        Schedule.video_id == video_id,
        Schedule.youtube_account_id == account_id,
        Schedule.status.in_(_ACTIVE_STATUSES),
    )
    if skip_id is not None:
        query = query.where(Schedule.id != skip_id)
    return bool(db.scalar(query))


def _check_plan(db, user, form: dict, skip_id: int | None):
    """Validate the whole form. Returns (video, account, when_utc, None)
    or (None, None, None, error message)."""
    try:
        video, account = _pick(db, user, form["video_id"], form["youtube_account_id"])
    except ValueError as exc:
        return None, None, None, str(exc)
    try:
        when_utc = local_input_to_utc(form["scheduled_at"])
    except ValueError as exc:
        return None, None, None, str(exc)
    problem = validate_when(when_utc)
    if problem:
        return None, None, None, problem
    if not video.title.strip():
        return None, None, None, "Give this video a title first (open the video and save its details)."
    if _already_planned(db, video.id, account.id, skip_id):
        return None, None, None, (
            "This video is already scheduled for that channel. "
            "Edit or delete the existing post instead."
        )
    return video, account, when_utc, None


def _form_page(
    request: Request,
    db: Session,
    user: User,
    form: dict,
    schedule: Schedule | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    now = utcnow()
    return _page(
        request, "schedule_form.html", status_code,
        schedule=schedule,
        form=form,
        error=error,
        videos=_my_videos(db, user),
        accounts=_my_accounts(db, user),
        min_value=to_input_value(now + timedelta(minutes=MIN_LEAD_MINUTES)),
        max_value=to_input_value(now + timedelta(days=MAX_DAYS_AHEAD)),
        tz_name=get_settings().APP_TIMEZONE,
        min_lead=MIN_LEAD_MINUTES,
    )


# ----------------------------------------------------------------- pages


@router.get("/schedules")
def schedule_list(
    request: Request,
    status: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    chosen = status if status in STATUSES else ""

    query = select(Schedule).where(Schedule.user_id == user.id)
    if chosen:
        query = query.where(Schedule.status == chosen)
    # Upcoming posts read best soonest-first; everything else newest-first.
    if chosen == STATUS_SCHEDULED:
        query = query.order_by(Schedule.scheduled_at_utc, Schedule.id)
    else:
        query = query.order_by(Schedule.scheduled_at_utc.desc(), Schedule.id.desc())
    schedules = db.scalars(query).all()

    counts = dict(
        db.execute(
            select(Schedule.status, func.count())
            .where(Schedule.user_id == user.id)
            .group_by(Schedule.status)
        ).all()
    )
    return _page(
        request, "schedules_list.html",
        schedules=schedules,
        statuses=STATUSES,
        chosen=chosen,
        counts=counts,
        total=sum(counts.values()),
        message=request.session.pop(_FLASH, None),
    )


@router.get("/schedules/new")
def schedule_new(
    request: Request,
    video_id: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    accounts = _my_accounts(db, user)
    form = {
        "video_id": video_id,
        "youtube_account_id": str(accounts[0].id) if len(accounts) == 1 else "",
        "scheduled_at": "",
    }
    return _form_page(request, db, user, form)


@router.post("/schedules")
def schedule_create(
    request: Request,
    video_id: str = Form(""),
    youtube_account_id: str = Form(""),
    scheduled_at: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    form = {
        "video_id": video_id,
        "youtube_account_id": youtube_account_id,
        "scheduled_at": scheduled_at,
    }
    video, account, when_utc, error = _check_plan(db, user, form, skip_id=None)
    if error:
        return _form_page(request, db, user, form, error=error, status_code=400)

    db.add(
        Schedule(
            user_id=user.id,
            video_id=video.id,
            youtube_account_id=account.id,
            scheduled_at_utc=when_utc,
            status=STATUS_SCHEDULED,
        )
    )
    db.commit()
    _flash(request, "ok", f"Scheduled for {format_local(when_utc)}.")
    return _back_to_list()


@router.get("/schedules/{schedule_id}/edit")
def schedule_edit_form(
    request: Request,
    schedule_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    schedule = _owned_schedule(db, user, schedule_id)
    if schedule.status != STATUS_SCHEDULED:
        _flash(request, "error", "Only posts that are still waiting can be edited.")
        return _back_to_list()
    form = {
        "video_id": str(schedule.video_id),
        "youtube_account_id": str(schedule.youtube_account_id),
        "scheduled_at": to_input_value(schedule.scheduled_at_utc),
    }
    return _form_page(request, db, user, form, schedule=schedule)


@router.post("/schedules/{schedule_id}/edit")
def schedule_edit(
    request: Request,
    schedule_id: int,
    video_id: str = Form(""),
    youtube_account_id: str = Form(""),
    scheduled_at: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    schedule = _owned_schedule(db, user, schedule_id)
    if schedule.status != STATUS_SCHEDULED:
        _flash(request, "error", "Only posts that are still waiting can be edited.")
        return _back_to_list()

    form = {
        "video_id": video_id,
        "youtube_account_id": youtube_account_id,
        "scheduled_at": scheduled_at,
    }
    video, account, when_utc, error = _check_plan(db, user, form, skip_id=schedule.id)
    if error:
        return _form_page(
            request, db, user, form, schedule=schedule, error=error, status_code=400
        )

    # One atomic UPDATE that only matches while the post is still 'scheduled'.
    # If the publisher worker claimed it a moment ago, nothing is changed.
    result = db.execute(
        update(Schedule)
        .where(Schedule.id == schedule.id, Schedule.status == STATUS_SCHEDULED)
        .values(
            video_id=video.id,
            youtube_account_id=account.id,
            scheduled_at_utc=when_utc,
        )
    )
    db.commit()
    if result.rowcount != 1:
        _flash(request, "error", "This post just started publishing, so it can no longer be edited.")
        return _back_to_list()

    _flash(request, "ok", f"Updated. New time: {format_local(when_utc)}.")
    return _back_to_list()


@router.post("/schedules/{schedule_id}/delete")
def schedule_delete(
    request: Request,
    schedule_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    schedule = _owned_schedule(db, user, schedule_id)
    if schedule.status == STATUS_PROCESSING:
        _flash(request, "error", "This post is being published right now. Try again in a minute.")
        return _back_to_list()

    # Stats first (they point at the schedule), then the schedule itself. The
    # status check sits in the DELETE, so a post the worker claimed in the
    # meantime is kept and the whole transaction is undone.
    db.execute(delete(VideoStat).where(VideoStat.schedule_id == schedule.id))
    result = db.execute(
        delete(Schedule).where(
            Schedule.id == schedule.id, Schedule.status != STATUS_PROCESSING
        )
    )
    if result.rowcount != 1:
        db.rollback()
        _flash(request, "error", "This post is being published right now. Try again in a minute.")
        return _back_to_list()
    db.commit()
    _flash(request, "ok", "Scheduled post deleted. (A video already on YouTube stays there.)")
    return _back_to_list()
