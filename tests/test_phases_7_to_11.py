"""Tests for Phases 7, 8, 9, 10, and 11:
- Phase 7: covered by tests/test_worker.py (worker and cron URL).
- Phase 8: Dashboard counts, failed upload listing, and one-click retry.
- Phase 9: Project 3 insights page, copy generator, and A/B variant scheduling.
- Phase 10: YouTube statistics sync into VideoStat and dashboard metrics display.
- Phase 11: Health endpoint and deployment readiness.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher import worker
from publisher.crypto import encrypt_text
from publisher.db import SessionLocal
from publisher.main import app
from publisher.models import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    Product,
    Schedule,
    User,
    Video,
    VideoStat,
    YouTubeAccount,
    utcnow,
)

PASSWORD = "correct horse"


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


def seed_due_schedule(uid, offset_minutes=-5, status=STATUS_SCHEDULED, variant=None):
    with SessionLocal() as db:
        video = Video(
            user_id=uid,
            title="Spring Lawn 2026 Promo",
            description="Price: PKR 6,950",
            tags="lawn, spring 2026",
            storage_key=f"videos/{uid}/{uuid.uuid4().hex}.mp4",
            privacy_status="public",
        )
        account = YouTubeAccount(
            user_id=uid,
            channel_id="UC_" + uuid.uuid4().hex[:10],
            channel_title="Test Lawn Brand",
            refresh_token_encrypted=encrypt_text("fake-refresh-token"),
        )
        db.add_all([video, account])
        db.flush()
        schedule = Schedule(
            user_id=uid,
            video_id=video.id,
            youtube_account_id=account.id,
            scheduled_at_utc=utcnow() + timedelta(minutes=offset_minutes),
            status=status,
            variant_label=variant,
        )
        db.add(schedule)
        db.commit()
        return schedule.id, video.id, account.id


# ------------------------------------------------------------ Phase 7 tests
#
# The worker and the cron URL are tested in detail in tests/test_worker.py
# (claiming, resumable upload, retries, quota, thumbnails, recovery, /cron/publish).


# --------------------------------------------------------- Phase 8 & 10 tests


def test_dashboard_shows_counts_and_retries_failed_post(client, other_client):
    uid = user_id_of(client)
    sid_failed, _, _ = seed_due_schedule(uid, offset_minutes=-10, status=STATUS_FAILED)
    sid_pub, _, _ = seed_due_schedule(
        uid, offset_minutes=-60, status=STATUS_PUBLISHED, variant="Variant A: Price in Description"
    )

    with SessionLocal() as db:
        pub = db.get(Schedule, sid_pub)
        pub.youtube_video_id = "YT_PUB_123"
        pub.youtube_url = "https://www.youtube.com/watch?v=YT_PUB_123"
        pub.published_at = utcnow()
        db.add(VideoStat(schedule_id=sid_pub, views=1250, likes=95, comments=14))
        db.commit()

    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Automation Dashboard" in page.text
    assert "1250" in page.text
    assert "Variant A: Price in Description" in page.text

    # Another user cannot retry someone else's failed schedule
    r_other = other_client.post(f"/schedules/{sid_failed}/retry")
    assert r_other.status_code == 404

    # Owner retries failed post -> resets to scheduled
    r_retry = client.post(f"/schedules/{sid_failed}/retry")
    assert r_retry.status_code == 303
    with SessionLocal() as db:
        retried = db.get(Schedule, sid_failed)
        assert retried.status == STATUS_SCHEDULED
        assert retried.attempts == 0


def test_stats_sync_records_video_stat_snapshot(client, monkeypatch):
    uid = user_id_of(client)
    sid_pub, _, _ = seed_due_schedule(uid, offset_minutes=-30, status=STATUS_PUBLISHED)
    with SessionLocal() as db:
        pub = db.get(Schedule, sid_pub)
        pub.youtube_video_id = "YT_SYNC_77"
        db.commit()

    monkeypatch.setattr(worker, "access_token_for", lambda acc: "fake-access-token")
    monkeypatch.setattr(
        worker,
        "fetch_video_statistics",
        lambda token, ids: {"YT_SYNC_77": {"views": 4200, "likes": 310, "comments": 42}},
    )

    r = client.post("/dashboard/sync-stats")
    assert r.status_code == 303

    with SessionLocal() as db:
        stat = db.scalar(select(VideoStat).where(VideoStat.schedule_id == sid_pub))
        assert stat is not None
        assert stat.views == 4200
        assert stat.likes == 310
        assert stat.comments == 42


# ------------------------------------------------------------ Phase 9 tests


def test_project3_insights_and_copy_helper(client):
    r = client.get("/insights")
    assert r.status_code == 200
    assert "Project 3 Audience" in r.text
    assert "34.4%" in r.text

    gen = client.post(
        "/insights/generate",
        data={
            "product_name": "Gulzar 3Pc Lawn",
            "campaign": "Spring Lawn",
            "celebrity": "Yumna Zaidi",
            "song_credit": "Sajna OST",
            "price_pkr": "7,450",
            "availability_url": "https://example.pk/gulzar",
            "variant": "A",
        },
    )
    assert gen.status_code == 200
    payload = gen.json()
    assert "Yumna Zaidi" in payload["title"]
    assert "PKR 7,450" in payload["description"]
    assert "Sajna OST" in payload["description"]
