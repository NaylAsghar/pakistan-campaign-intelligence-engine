"""Phase 6 tests: time zone helpers and the scheduling pages.

The expected times are worked out with a fixed UTC+5 offset (Pakistan has no
daylight saving), not with the app's own helpers, so the tests check the app
instead of repeating it.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher.db import SessionLocal
from publisher.main import app
from publisher.models import Schedule, User, Video, VideoStat, YouTubeAccount, utcnow
from publisher.timeutil import (
    format_local,
    local_input_to_utc,
    to_input_value,
    utc_to_local,
)

PASSWORD = "correct horse"
KARACHI = timezone(timedelta(hours=5))
UTC = timezone.utc


# ----------------------------------------------------------------- helpers


def make_client():
    return TestClient(app, follow_redirects=False)


def sign_up(client):
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/register",
        data={"email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert r.status_code == 303
    return email


@pytest.fixture()
def client():
    with make_client() as c:
        c.email = sign_up(c)
        yield c


@pytest.fixture()
def other_client():
    with make_client() as c:
        c.email = sign_up(c)
        yield c


def user_id_of(client):
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == client.email))


def add_video(uid, title="Eid lawn promo"):
    with SessionLocal() as db:
        video = Video(
            user_id=uid, title=title,
            storage_key=f"videos/{uid}/{uuid.uuid4().hex}.mp4",
        )
        db.add(video)
        db.commit()
        return video.id


def add_account(uid, title="Lawn Channel"):
    with SessionLocal() as db:
        account = YouTubeAccount(
            user_id=uid, channel_id="UC_" + uuid.uuid4().hex[:10],
            channel_title=title, refresh_token_encrypted="not-a-real-token",
        )
        db.add(account)
        db.commit()
        return account.id


def add_schedule(uid, video_id, account_id, status="scheduled", days=5):
    with SessionLocal() as db:
        row = Schedule(
            user_id=uid, video_id=video_id, youtube_account_id=account_id,
            scheduled_at_utc=utcnow() + timedelta(days=days), status=status,
        )
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture()
def setup(client):
    """(user id, video id, channel id) for the logged-in user."""
    uid = user_id_of(client)
    return uid, add_video(uid), add_account(uid)


def future(days=10, hour=20, minute=30):
    """('2026-12-08T20:30', the same moment in UTC) a few days from now."""
    day = (utcnow().astimezone(KARACHI) + timedelta(days=days)).date()
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KARACHI)
    return local.strftime("%Y-%m-%dT%H:%M"), local.astimezone(UTC)


def plan(video_id, account_id, when):
    return {"video_id": str(video_id), "youtube_account_id": str(account_id), "scheduled_at": when}


def get_schedule(schedule_id):
    with SessionLocal() as db:
        return db.get(Schedule, schedule_id)


# ----------------------------------------------------------- time helpers


def test_karachi_time_becomes_utc():
    assert local_input_to_utc("2026-12-01T20:30") == datetime(2026, 12, 1, 15, 30, tzinfo=UTC)
    assert local_input_to_utc("2026-12-01T00:15") == datetime(2026, 11, 30, 19, 15, tzinfo=UTC)
    assert local_input_to_utc(" 2026-12-01 20:30 ") == datetime(2026, 12, 1, 15, 30, tzinfo=UTC)
    # seconds are dropped: schedules run to the minute
    assert local_input_to_utc("2026-12-01T20:30:45") == datetime(2026, 12, 1, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["", "tomorrow", "2026-13-01T10:00", "2026-12-01", "01/12/2026 10:00"])
def test_bad_time_text_is_rejected(bad):
    with pytest.raises(ValueError):
        local_input_to_utc(bad)


def test_utc_back_to_karachi():
    moment = datetime(2026, 12, 1, 15, 30, tzinfo=UTC)
    assert utc_to_local(moment).hour == 20
    assert to_input_value(moment) == "2026-12-01T20:30"
    assert to_input_value(None) == ""
    assert format_local(moment).startswith("01 Dec 2026, 08:30 PM")
    assert format_local(None) == ""
    # a naive value (SQLite can return one) is treated as UTC
    assert to_input_value(datetime(2026, 12, 1, 15, 30)) == "2026-12-01T20:30"


def test_round_trip():
    typed = "2027-03-04T09:05"
    assert to_input_value(local_input_to_utc(typed)) == typed


# ------------------------------------------------------------ login rules


def test_pages_need_login():
    with make_client() as anon:
        for path in ("/schedules", "/schedules/new", "/schedules/1/edit"):
            r = anon.get(path)
            assert r.status_code == 303 and r.headers["location"] == "/login", path
        assert anon.post("/schedules", data={}).status_code == 303
        assert anon.post("/schedules/1/edit", data={}).status_code == 303
        assert anon.post("/schedules/1/delete").status_code == 303


# ----------------------------------------------------------- new schedule


def test_new_page_explains_what_is_missing(client):
    page = client.get("/schedules/new")
    assert page.status_code == 200 and "Upload a video" in page.text

    uid = user_id_of(client)
    add_video(uid)
    assert "Connect a channel" in client.get("/schedules/new").text

    add_account(uid)
    page = client.get("/schedules/new")
    assert 'type="datetime-local"' in page.text and "Asia/Karachi" in page.text


def test_new_page_preselects_the_video_and_single_channel(client, setup):
    _, video_id, account_id = setup
    page = client.get(f"/schedules/new?video_id={video_id}")
    assert f'<option value="{video_id}" selected>' in page.text
    assert f'<option value="{account_id}" selected>' in page.text


def test_create_stores_utc_and_shows_karachi_time(client, setup):
    uid, video_id, account_id = setup
    typed, expected_utc = future(10, 20, 30)
    r = client.post("/schedules", data=plan(video_id, account_id, typed))
    assert r.status_code == 303 and r.headers["location"] == "/schedules"

    with SessionLocal() as db:
        row = db.scalars(select(Schedule).where(Schedule.user_id == uid)).one()
        assert row.scheduled_at_utc == expected_utc
        assert row.status == "scheduled" and row.attempts == 0
        assert row.video_id == video_id and row.youtube_account_id == account_id

    page = client.get("/schedules")
    assert "Scheduled for" in page.text  # the one-time message
    assert "08:30 PM" in page.text  # shown in Karachi time, not 03:30 PM UTC
    assert "Eid lawn promo" in page.text and "Lawn Channel" in page.text
    assert "Scheduled for" not in client.get("/schedules").text


@pytest.mark.parametrize(
    "when, expected",
    [
        ("", "Enter the date and time"),
        ("tomorrow evening", "Enter the date and time"),
        ((utcnow().astimezone(KARACHI) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"), "at least 5 minutes"),
        ((utcnow().astimezone(KARACHI) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"), "at least 5 minutes"),
        ((utcnow().astimezone(KARACHI) + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M"), "within the next 365 days"),
    ],
)
def test_create_rejects_bad_times(client, setup, when, expected):
    _, video_id, account_id = setup
    r = client.post("/schedules", data=plan(video_id, account_id, when))
    assert r.status_code == 400 and expected in r.text
    with SessionLocal() as db:  # nothing was saved for this user
        assert db.scalars(select(Schedule).where(Schedule.user_id == user_id_of(client))).all() == []


def test_failed_create_keeps_what_the_user_chose(client, setup):
    _, video_id, account_id = setup
    r = client.post("/schedules", data=plan(video_id, account_id, "nonsense"))
    assert r.status_code == 400
    assert f'<option value="{video_id}" selected>' in r.text
    assert f'<option value="{account_id}" selected>' in r.text


def test_create_rejects_missing_or_foreign_video_and_channel(client, other_client, setup):
    _, video_id, account_id = setup
    other_uid = user_id_of(other_client)
    foreign_video = add_video(other_uid)
    foreign_account = add_account(other_uid)
    typed, _ = future()

    for data in (
        plan("", account_id, typed),
        plan("abc", account_id, typed),
        plan(99999, account_id, typed),
        plan(foreign_video, account_id, typed),
        plan(video_id, "", typed),
        plan(video_id, 99999, typed),
        plan(video_id, foreign_account, typed),
    ):
        r = client.post("/schedules", data=data)
        assert r.status_code == 400, data
        assert "Choose a" in r.text


def test_video_without_a_title_cannot_be_scheduled(client):
    uid = user_id_of(client)
    video_id, account_id = add_video(uid, title="  "), add_account(uid)
    r = client.post("/schedules", data=plan(video_id, account_id, future()[0]))
    assert r.status_code == 400 and "Give this video a title" in r.text


def test_same_video_cannot_be_planned_twice_for_one_channel(client, setup):
    uid, video_id, account_id = setup
    typed, _ = future()
    assert client.post("/schedules", data=plan(video_id, account_id, typed)).status_code == 303
    r = client.post("/schedules", data=plan(video_id, account_id, future(11)[0]))
    assert r.status_code == 400 and "already scheduled" in r.text

    # a different channel is fine
    second_account = add_account(uid, "Second channel")
    assert client.post("/schedules", data=plan(video_id, second_account, typed)).status_code == 303


def test_a_published_post_does_not_block_a_new_one(client, setup):
    uid, video_id, account_id = setup
    add_schedule(uid, video_id, account_id, status="published", days=-3)
    add_schedule(uid, video_id, account_id, status="failed", days=-2)
    r = client.post("/schedules", data=plan(video_id, account_id, future()[0]))
    assert r.status_code == 303


# --------------------------------------------------------------- the list


def test_list_filters_by_status_and_counts(client, setup):
    uid, video_id, account_id = setup
    other_video = add_video(uid, "Second promo")
    add_schedule(uid, video_id, account_id, "scheduled")
    add_schedule(uid, other_video, account_id, "published", days=-1)
    add_schedule(uid, other_video, account_id, "failed", days=-2)

    page = client.get("/schedules").text
    assert "All (3)" in page and "Scheduled (1)" in page and "Published (1)" in page
    only = client.get("/schedules?status=published").text
    assert "Second promo" in only and "Eid lawn promo" not in only
    assert "No posts with this status" in client.get("/schedules?status=processing").text
    # an unknown filter shows everything
    assert "Eid lawn promo" in client.get("/schedules?status=bogus").text


def test_list_only_shows_my_posts(client, other_client, setup):
    uid, video_id, account_id = setup
    add_schedule(uid, video_id, account_id)
    assert "Eid lawn promo" in client.get("/schedules").text
    page = other_client.get("/schedules").text
    assert "Eid lawn promo" not in page and "Nothing is scheduled yet" in page


def test_list_links_to_published_video(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id, "published", days=-1)
    with SessionLocal() as db:
        db.get(Schedule, sid).youtube_url = "https://www.youtube.com/watch?v=abc123"
        db.commit()
    page = client.get("/schedules").text
    assert "https://www.youtube.com/watch?v=abc123" in page and "Watch on YouTube" in page
    assert "/edit" not in page  # a published post cannot be edited


def test_list_shows_the_failure_reason(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id, "failed", days=-1)
    with SessionLocal() as db:
        db.get(Schedule, sid).error_message = "quotaExceeded"
        db.commit()
    assert "quotaExceeded" in client.get("/schedules").text


# ------------------------------------------------------------------- edit


def test_edit_changes_time_video_and_channel(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    new_video, new_account = add_video(uid, "Another promo"), add_account(uid, "Another channel")

    page = client.get(f"/schedules/{sid}/edit")
    assert page.status_code == 200 and "Save changes" in page.text
    assert f'<option value="{video_id}" selected>' in page.text

    typed, expected_utc = future(20, 9, 5)
    r = client.post(f"/schedules/{sid}/edit", data=plan(new_video, new_account, typed))
    assert r.status_code == 303 and r.headers["location"] == "/schedules"
    row = get_schedule(sid)
    assert row.scheduled_at_utc == expected_utc
    assert row.video_id == new_video and row.youtube_account_id == new_account
    assert "Updated" in client.get("/schedules").text


def test_edit_form_is_filled_with_karachi_time(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    with SessionLocal() as db:
        db.get(Schedule, sid).scheduled_at_utc = datetime(2030, 1, 2, 15, 30, tzinfo=UTC)
        db.commit()
    assert 'value="2030-01-02T20:30"' in client.get(f"/schedules/{sid}/edit").text


def test_edit_validation_keeps_the_old_values(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    before = get_schedule(sid).scheduled_at_utc
    r = client.post(f"/schedules/{sid}/edit", data=plan(video_id, account_id, "2001-01-01T10:00"))
    assert r.status_code == 400 and "at least 5 minutes" in r.text
    assert get_schedule(sid).scheduled_at_utc == before


def test_editing_a_post_does_not_trip_the_duplicate_check(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    r = client.post(f"/schedules/{sid}/edit", data=plan(video_id, account_id, future(12)[0]))
    assert r.status_code == 303


@pytest.mark.parametrize("status", ["processing", "published", "failed"])
def test_only_waiting_posts_can_be_edited(client, setup, status):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id, status=status)
    before = get_schedule(sid).scheduled_at_utc

    r = client.get(f"/schedules/{sid}/edit")
    assert r.status_code == 303 and r.headers["location"] == "/schedules"
    assert "Only posts that are still waiting" in client.get("/schedules").text

    r = client.post(f"/schedules/{sid}/edit", data=plan(video_id, account_id, future()[0]))
    assert r.status_code == 303
    row = get_schedule(sid)
    assert row.status == status and row.scheduled_at_utc == before


# ----------------------------------------------------------------- delete


def test_delete_a_scheduled_post(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    r = client.post(f"/schedules/{sid}/delete")
    assert r.status_code == 303 and r.headers["location"] == "/schedules"
    assert get_schedule(sid) is None
    assert "Scheduled post deleted" in client.get("/schedules").text


def test_delete_removes_stats_of_a_published_post(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id, "published", days=-1)
    with SessionLocal() as db:
        db.add(VideoStat(schedule_id=sid, views=10, likes=2, comments=1))
        db.commit()
    assert client.post(f"/schedules/{sid}/delete").status_code == 303
    with SessionLocal() as db:
        assert db.get(Schedule, sid) is None
        assert db.scalars(select(VideoStat).where(VideoStat.schedule_id == sid)).all() == []


def test_a_post_being_published_cannot_be_deleted(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id, "processing", days=0)
    r = client.post(f"/schedules/{sid}/delete")
    assert r.status_code == 303
    assert get_schedule(sid) is not None
    assert "being published right now" in client.get("/schedules").text


def test_deleting_the_post_frees_the_video_for_deletion_and_rescheduling(client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    assert client.post(f"/videos/{video_id}/delete").status_code == 303
    assert get_schedule(sid) is not None  # blocked: a schedule still uses the video
    client.post(f"/schedules/{sid}/delete")
    assert client.post(f"/videos/{video_id}/delete").headers["location"] == "/videos"
    with SessionLocal() as db:
        assert db.get(Video, video_id) is None


# --------------------------------------------------------------- ownership


def test_other_users_cannot_touch_my_post(client, other_client, setup):
    uid, video_id, account_id = setup
    sid = add_schedule(uid, video_id, account_id)
    before = get_schedule(sid).scheduled_at_utc

    assert other_client.get(f"/schedules/{sid}/edit").status_code == 404
    r = other_client.post(f"/schedules/{sid}/edit", data=plan(video_id, account_id, future()[0]))
    assert r.status_code == 404
    assert other_client.post(f"/schedules/{sid}/delete").status_code == 404
    row = get_schedule(sid)
    assert row is not None and row.scheduled_at_utc == before


# --------------------------------------------------------------- navigation


def test_navigation_and_video_page_link_to_scheduling(client, setup):
    _, video_id, _ = setup
    assert 'href="/schedules"' in client.get("/videos").text
    assert f'/schedules/new?video_id={video_id}' in client.get(f"/videos/{video_id}").text
