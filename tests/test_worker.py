"""Phase 7 tests: the publisher worker, the YouTube upload layer, the cron URL.

No test touches the internet. A FakeYouTube object replaces the one network
function (youtube_api._request) and behaves like YouTube's resumable upload
service: it hands out session addresses, accepts chunks, reports progress and
can be told to fail. Files live in a temporary LocalStorage folder.
"""

import os
import threading
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, inspect, select, text, update

from publisher import cron, google_oauth, worker, youtube_api
from publisher import db as db_module
from publisher.crypto import encrypt_text
from publisher.db import SessionLocal, init_db
from publisher.google_oauth import YouTubeError
from publisher.main import app
from publisher.models import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    Schedule,
    User,
    Video,
    VideoStat,
    YouTubeAccount,
    utcnow,
)
from publisher.storage import LocalStorage
from publisher.youtube_api import PublishError

KB = 1024


# ------------------------------------------------------------ fake YouTube


class FakeYouTube:
    """Stands in for youtube_api._request. Records everything it is asked."""

    def __init__(self):
        self.calls = []  # (method, url)
        self.metadata = []  # JSON bodies sent to start a session
        self.sessions = {}  # session url -> {"total": n, "data": bytearray}
        self.sessions_started = 0
        self.chunk_ranges = []  # (start, end, total) of every accepted chunk request
        self.progress_queries = 0
        self.init_response = None  # (status, json_body) to answer session start with
        self.put_failures = []  # one entry per chunk PUT: None = fine, int = that status
        self.thumb_status = 200
        self.thumbnail_requests = []
        self.video_id = "VID123"
        self.finish_without_id = False

    # --- helpers for the tests
    def add_session(self, url, total, data=b""):
        self.sessions[url] = {"total": total, "data": bytearray(data)}

    def uploaded(self):
        """The bytes of the only session."""
        (session,) = self.sessions.values()
        return bytes(session["data"])

    # --- the fake service
    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        if method == "POST" and url == youtube_api.UPLOAD_INIT_URL:
            return self._start(kwargs)
        if method == "POST" and url == youtube_api.THUMBNAIL_URL:
            self.thumbnail_requests.append(
                {
                    "params": kwargs["params"],
                    "content_type": kwargs["headers"]["Content-Type"],
                    "size": len(kwargs["content"]),
                }
            )
            if self.thumb_status == 200:
                return httpx.Response(200, json={"items": []})
            return _error(self.thumb_status, "forbidden")
        if method == "PUT":
            return self._put(url, kwargs["headers"], kwargs.get("content", b""))
        raise AssertionError(f"unexpected request {method} {url}")

    def _start(self, kwargs):
        if self.init_response:
            status, body = self.init_response
            return httpx.Response(status, json=body)
        import json

        self.metadata.append(json.loads(kwargs["content"]))
        self.sessions_started += 1
        url = f"{youtube_api.GOOGLE_UPLOAD_PREFIX}upload/youtube/v3/videos?upload_id={uuid.uuid4().hex}"
        self.add_session(url, int(kwargs["headers"]["X-Upload-Content-Length"]))
        return httpx.Response(200, headers={"Location": url})

    def _put(self, url, headers, content):
        session = self.sessions.get(url)
        if session is None:
            return _error(404, "notFound")
        content_range = headers["Content-Range"]
        if content_range.startswith("bytes */"):
            self.progress_queries += 1
            return self._status(session)
        if self.put_failures:
            outcome = self.put_failures.pop(0)
            if outcome is not None:
                return _error(outcome, "backendError")
        start, end, total = (
            int(n)
            for n in content_range.replace("bytes ", "").replace("-", " ").replace("/", " ").split()
        )
        if start != len(session["data"]):
            return self._status(session)  # out of sync: tell the client the real offset
        self.chunk_ranges.append((start, end, total))
        session["data"] += content
        return self._status(session)

    def _status(self, session):
        if len(session["data"]) == session["total"]:
            if self.finish_without_id:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"id": self.video_id})
        headers = {}
        if session["data"]:
            headers["Range"] = f"bytes=0-{len(session['data']) - 1}"
        return httpx.Response(308, headers=headers)


def _error(status, reason):
    return httpx.Response(
        status,
        json={"error": {"code": status, "message": f"{reason} message", "errors": [{"reason": reason}]}},
    )


# --------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Clean slate for every test: no old schedules, fake YouTube, temp storage."""
    init_db()  # tables exist even when this file runs on its own
    with SessionLocal() as db:
        db.execute(delete(VideoStat))
        db.execute(delete(Schedule))
        db.commit()

    storage = LocalStorage(tmp_path / "storage", "test-secret-key")
    fake = FakeYouTube()
    monkeypatch.setattr(worker, "get_storage", lambda: storage)
    monkeypatch.setattr(youtube_api, "_request", fake)
    monkeypatch.setattr(youtube_api, "_sleep", lambda seconds: None)
    monkeypatch.setattr(youtube_api, "CHUNK_SIZE", 256 * KB)  # small: files need several chunks
    monkeypatch.setattr(google_oauth, "access_token_for", lambda account: "access-token-xyz")
    return SimpleNamespace(storage=storage, yt=fake)


def make_user():
    with SessionLocal() as db:
        user = User(email=f"w-{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        return user.id


def make_post(
    env,
    *,
    size=600 * KB,
    when=None,
    status=STATUS_SCHEDULED,
    thumbnail=False,
    title="Eid lawn promo",
    privacy="public",
    tags="lawn, eid",
    write_file=True,
    session_url=None,
    youtube_video_id=None,
    attempts=0,
    claimed_at=None,
    next_retry_at=None,
):
    """A video file in storage plus the Schedule row for it. Returns the schedule id."""
    uid = make_user()
    key = f"videos/{uid}/{uuid.uuid4().hex}.mp4"
    if write_file:
        env.storage.save(key, os.urandom(size))
    thumb_key = None
    if thumbnail:
        thumb_key = f"thumbnails/{uid}/{uuid.uuid4().hex}.jpg"
        env.storage.save(thumb_key, b"\xff\xd8\xff" + os.urandom(2000))
    with SessionLocal() as db:
        account = YouTubeAccount(
            user_id=uid,
            channel_id="UC_" + uuid.uuid4().hex[:10],
            channel_title="Lawn Channel",
            refresh_token_encrypted=encrypt_text("refresh-token-abc"),
        )
        video = Video(
            user_id=uid, storage_key=key, title=title, description="Fresh lawn prints",
            tags=tags, privacy_status=privacy, thumbnail_key=thumb_key, size_bytes=size,
        )
        db.add_all([account, video])
        db.flush()
        schedule = Schedule(
            user_id=uid,
            video_id=video.id,
            youtube_account_id=account.id,
            scheduled_at_utc=when or utcnow() - timedelta(minutes=1),
            status=status,
            attempts=attempts,
            claimed_at=claimed_at,
            next_retry_at=next_retry_at,
            upload_session_url=session_url,
            youtube_video_id=youtube_video_id,
        )
        db.add(schedule)
        db.commit()
        return schedule.id


def get(schedule_id):
    with SessionLocal() as db:
        return db.get(Schedule, schedule_id)


def source_bytes(env, schedule_id):
    with SessionLocal() as db:
        schedule = db.get(Schedule, schedule_id)
        key = schedule.video.storage_key
    return (env.storage.root / key).read_bytes()


def minutes_until(moment):
    return (moment - utcnow()).total_seconds() / 60


# ------------------------------------------------------------- claiming


def test_claim_picks_only_due_posts(env):
    due = make_post(env)
    make_post(env, when=utcnow() + timedelta(hours=2))  # not yet
    make_post(env, next_retry_at=utcnow() + timedelta(minutes=30))  # waiting to retry
    make_post(env, status=STATUS_PROCESSING)  # someone else has it
    make_post(env, status=STATUS_PUBLISHED)
    with SessionLocal() as db:
        assert worker.claim_next(db) == due
        assert worker.claim_next(db) is None
    claimed = get(due)
    assert claimed.status == STATUS_PROCESSING
    assert claimed.attempts == 1
    assert claimed.claimed_at is not None


def test_claim_takes_oldest_first(env):
    newer = make_post(env, when=utcnow() - timedelta(minutes=5))
    older = make_post(env, when=utcnow() - timedelta(hours=3))
    with SessionLocal() as db:
        assert worker.claim_next(db) == older
        assert worker.claim_next(db) == newer


def test_retry_post_is_claimed_once_its_wait_is_over(env):
    sid = make_post(env, attempts=1, next_retry_at=utcnow() - timedelta(seconds=1))
    with SessionLocal() as db:
        assert worker.claim_next(db) == sid
    assert get(sid).attempts == 2


def test_only_one_worker_wins_a_post(env):
    """Eight workers race for one due post: exactly one gets it."""
    make_post(env)
    winners, barrier = [], threading.Barrier(8)

    def race():
        barrier.wait()
        with SessionLocal() as db:
            winners.append(worker.claim_next(db))

    threads = [threading.Thread(target=race) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([w for w in winners if w is not None]) == 1


# ---------------------------------------------------------- publishing


def test_publish_success(env):
    sid = make_post(env, size=600 * KB, privacy="unlisted", tags="lawn, eid, 3-piece")
    summary = worker.run_once()
    assert (summary.published, summary.retried, summary.failed) == (1, 0, 0)

    done = get(sid)
    assert done.status == STATUS_PUBLISHED
    assert done.youtube_video_id == "VID123"
    assert done.youtube_url == "https://www.youtube.com/watch?v=VID123"
    assert done.published_at is not None
    assert done.attempts == 1
    assert done.error_message is None
    assert done.upload_session_url is None and done.claimed_at is None

    # the exact file arrived, in contiguous chunks
    assert env.yt.uploaded() == source_bytes(env, sid)
    assert len(env.yt.chunk_ranges) == 3
    assert env.yt.chunk_ranges[0][0] == 0
    for previous, current in zip(env.yt.chunk_ranges, env.yt.chunk_ranges[1:]):
        assert current[0] == previous[1] + 1

    # the metadata YouTube received
    (meta,) = env.yt.metadata
    assert meta["snippet"]["title"] == "Eid lawn promo"
    assert meta["snippet"]["description"] == "Fresh lawn prints"
    assert meta["snippet"]["tags"] == ["lawn", "eid", "3-piece"]
    assert meta["status"]["privacyStatus"] == "unlisted"
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    assert env.yt.thumbnail_requests == []  # this video has no thumbnail


def test_post_that_is_not_due_is_left_alone(env):
    sid = make_post(env, when=utcnow() + timedelta(hours=1))
    summary = worker.run_once()
    assert summary.processed == 0
    assert get(sid).status == STATUS_SCHEDULED and get(sid).attempts == 0
    assert env.yt.calls == []


def test_two_due_posts_are_both_published_oldest_first(env):
    first = make_post(env, when=utcnow() - timedelta(hours=2), size=300 * KB)
    second = make_post(env, when=utcnow() - timedelta(hours=1), size=300 * KB)
    env.yt.video_id = "ONLY_ONE_ID"  # both get this fake id; we only check order and count
    summary = worker.run_once()
    assert summary.published == 2
    assert get(first).published_at <= get(second).published_at


def test_run_once_respects_max_jobs(env):
    make_post(env, size=300 * KB)
    make_post(env, size=300 * KB)
    assert worker.run_once(max_jobs=1).processed == 1
    assert worker.run_once(max_jobs=1).processed == 1
    assert worker.run_once(max_jobs=1).processed == 0


def test_thumbnail_is_uploaded(env):
    make_post(env, size=300 * KB, thumbnail=True)
    worker.run_once()
    (request,) = env.yt.thumbnail_requests
    assert request["params"]["videoId"] == "VID123"
    assert request["content_type"] == "image/jpeg"
    assert request["size"] > 1000


def test_thumbnail_failure_does_not_fail_the_post(env):
    env.yt.thumb_status = 403
    sid = make_post(env, size=300 * KB, thumbnail=True)
    summary = worker.run_once()
    assert summary.published == 1
    done = get(sid)
    assert done.status == STATUS_PUBLISHED
    assert done.youtube_url.endswith("VID123")
    assert done.error_message.startswith("Published, but the thumbnail was not set")


# ------------------------------------------------- failures and retries


def test_temporary_error_is_retried_later(env):
    env.yt.init_response = (503, {"error": {"code": 503, "message": "down"}})
    sid = make_post(env)
    summary = worker.run_once()
    assert (summary.published, summary.retried, summary.failed) == (0, 1, 0)

    after = get(sid)
    assert after.status == STATUS_SCHEDULED
    assert after.attempts == 1
    assert 1.5 < minutes_until(after.next_retry_at) < 2.5  # first wait: 2 minutes
    assert after.error_message.startswith("Attempt 1 of 5 failed")
    assert "access-token-xyz" not in after.error_message

    # not picked up again while it waits
    assert worker.run_once().processed == 0

    # once the wait is over and YouTube is back, it goes through
    env.yt.init_response = None
    with SessionLocal() as db:
        db.execute(update(Schedule).where(Schedule.id == sid).values(next_retry_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert worker.run_once().published == 1
    final = get(sid)
    assert final.status == STATUS_PUBLISHED and final.attempts == 2
    assert final.error_message is None and final.next_retry_at is None


def test_backoff_grows_then_post_fails_after_five_attempts(env):
    env.yt.init_response = (500, {"error": {"code": 500, "message": "down"}})
    sid = make_post(env)
    waits = []
    for attempt in range(1, 6):
        with SessionLocal() as db:
            db.execute(update(Schedule).where(Schedule.id == sid).values(next_retry_at=None))
            db.commit()
        worker.run_once()
        after = get(sid)
        assert after.attempts == attempt
        if after.status == STATUS_SCHEDULED:
            waits.append(round(minutes_until(after.next_retry_at)))
    assert waits == [2, 10, 30, 120]
    final = get(sid)
    assert final.status == STATUS_FAILED
    assert final.error_message.startswith("Gave up after 5 attempts")
    assert final.next_retry_at is None
    assert worker.run_once().processed == 0  # failed posts are never retried automatically


def test_permanent_error_fails_at_once(env):
    env.yt.init_response = (400, {"error": {"code": 400, "message": "Invalid title", "errors": [{"reason": "invalidTitle"}]}})
    sid = make_post(env)
    summary = worker.run_once()
    assert (summary.retried, summary.failed) == (0, 1)
    after = get(sid)
    assert after.status == STATUS_FAILED and after.attempts == 1
    assert "refused" in after.error_message and "Invalid title" in after.error_message


def test_quota_error_waits_three_hours(env):
    env.yt.init_response = (403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    sid = make_post(env)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_SCHEDULED
    assert 179 < minutes_until(after.next_retry_at) < 181
    assert "quota" in after.error_message.lower()


def test_missing_video_file_fails_permanently(env):
    sid = make_post(env, write_file=False)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_FAILED
    assert "missing from storage" in after.error_message
    assert env.yt.calls == []


def test_storage_problem_is_retried(env, monkeypatch):
    sid = make_post(env)

    def broken(key, destination):
        raise RuntimeError("bucket is unreachable")

    monkeypatch.setattr(env.storage, "download_to_path", broken)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_SCHEDULED
    assert "storage" in after.error_message
    assert "unreachable" not in after.error_message  # internal detail stays in the log


def test_revoked_youtube_access_fails_with_reconnect_hint(env, monkeypatch):
    def revoked(account):
        raise YouTubeError("Token has been expired or revoked.", code="invalid_grant")

    monkeypatch.setattr(google_oauth, "access_token_for", revoked)
    sid = make_post(env)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_FAILED
    assert "Reconnect the channel" in after.error_message


def test_google_outage_while_refreshing_token_is_retried(env, monkeypatch):
    def down(account):
        raise YouTubeError("Could not reach Google. Please try again.")

    monkeypatch.setattr(google_oauth, "access_token_for", down)
    sid = make_post(env)
    worker.run_once()
    assert get(sid).status == STATUS_SCHEDULED


def test_unexpected_bug_becomes_a_retry_without_leaking_details(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(youtube_api, "upload_video", boom)
    sid = make_post(env)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_SCHEDULED
    assert "secret internal detail" not in after.error_message
    assert "server log" in after.error_message


# --------------------------------------------------------------- resume


def test_interrupted_upload_resumes_where_it_stopped(env):
    # chunk 1 arrives; chunk 2 fails on all three tries -> the attempt fails
    env.yt.put_failures = [None, 503, 503, 503]
    sid = make_post(env, size=600 * KB)
    worker.run_once()

    mid = get(sid)
    assert mid.status == STATUS_SCHEDULED and mid.attempts == 1
    assert mid.upload_session_url and mid.upload_session_url.startswith("https://www.googleapis.com/")
    assert len(env.yt.uploaded()) == 256 * KB

    # next attempt: no new session, asks YouTube for progress, sends only the rest
    with SessionLocal() as db:
        db.execute(update(Schedule).where(Schedule.id == sid).values(next_retry_at=None))
        db.commit()
    env.yt.chunk_ranges.clear()
    queries_before = env.yt.progress_queries
    assert worker.run_once().published == 1

    assert env.yt.sessions_started == 1
    assert env.yt.progress_queries == queries_before + 1
    assert env.yt.chunk_ranges[0][0] == 256 * KB  # did NOT start from byte 0
    assert env.yt.uploaded() == source_bytes(env, sid)
    done = get(sid)
    assert done.status == STATUS_PUBLISHED and done.attempts == 2
    assert done.upload_session_url is None


def test_short_network_blip_is_fixed_inside_one_attempt(env):
    env.yt.put_failures = [None, 503, None, None]
    sid = make_post(env, size=600 * KB)
    assert worker.run_once().published == 1
    assert get(sid).attempts == 1
    assert env.yt.uploaded() == source_bytes(env, sid)


def test_expired_session_is_replaced(env):
    stale = f"{youtube_api.GOOGLE_UPLOAD_PREFIX}upload/youtube/v3/videos?upload_id=forgotten"
    sid = make_post(env, session_url=stale, attempts=1)
    assert worker.run_once().published == 1
    assert env.yt.sessions_started == 1
    assert env.yt.uploaded() == source_bytes(env, sid)


def test_saved_address_outside_google_is_never_used(env):
    sid = make_post(env, session_url="https://evil.example/steal?token=1")
    assert worker.run_once().published == 1
    assert all("evil.example" not in url for _method, url in env.yt.calls)
    assert env.yt.sessions_started == 1
    assert get(sid).status == STATUS_PUBLISHED


def test_video_already_on_youtube_is_not_uploaded_again(env):
    sid = make_post(env, youtube_video_id="OLDVID", attempts=1)
    assert worker.run_once().published == 1
    assert env.yt.sessions_started == 0
    done = get(sid)
    assert done.youtube_video_id == "OLDVID"
    assert done.status == STATUS_PUBLISHED


def test_youtube_finishing_without_a_video_id_is_not_retried(env):
    env.yt.finish_without_id = True
    sid = make_post(env, size=300 * KB)
    worker.run_once()
    after = get(sid)
    assert after.status == STATUS_FAILED
    assert "did not return a video id" in after.error_message


def test_ctrl_c_hands_the_post_back_and_keeps_the_resume_point(env, monkeypatch):
    session = f"{youtube_api.GOOGLE_UPLOAD_PREFIX}upload/youtube/v3/videos?upload_id=halfway"
    sid = make_post(env, session_url=session)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(youtube_api, "upload_video", interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.run_once()
    after = get(sid)
    assert after.status == STATUS_SCHEDULED
    assert after.attempts == 0 and after.claimed_at is None  # the attempt does not count
    assert after.upload_session_url == session


# ---------------------------------------------------- abandoned posts


def test_abandoned_post_goes_back_to_scheduled(env):
    old = utcnow() - timedelta(minutes=30)
    session = f"{youtube_api.GOOGLE_UPLOAD_PREFIX}upload/youtube/v3/videos?upload_id=keepme"
    sid = make_post(env, status=STATUS_PROCESSING, attempts=1, claimed_at=old, session_url=session)
    with SessionLocal() as db:
        assert worker.recover_stale(db) == 1
    after = get(sid)
    assert after.status == STATUS_SCHEDULED and after.claimed_at is None
    assert after.upload_session_url == session  # progress is kept for the resume


def test_abandoned_post_with_no_tries_left_fails(env):
    sid = make_post(env, status=STATUS_PROCESSING, attempts=5, claimed_at=utcnow() - timedelta(hours=1))
    with SessionLocal() as db:
        worker.recover_stale(db)
    after = get(sid)
    assert after.status == STATUS_FAILED and "stopped" in after.error_message


def test_post_being_worked_on_is_not_recovered(env):
    sid = make_post(env, status=STATUS_PROCESSING, attempts=1, claimed_at=utcnow() - timedelta(minutes=1))
    with SessionLocal() as db:
        assert worker.recover_stale(db) == 0
    assert get(sid).status == STATUS_PROCESSING


# ------------------------------------------------------- command line


def test_command_line_once(env, capsys):
    make_post(env, size=300 * KB)
    worker.main(["--once"])
    assert "published=1" in capsys.readouterr().out


# ------------------------------------------------ youtube_api details


@pytest.mark.parametrize(
    "status, reason, retryable",
    [
        (400, "invalidTitle", False),
        (401, "authError", True),
        (403, "forbidden", False),
        (403, "quotaExceeded", True),
        (404, "videoNotFound", False),
        (429, "", True),
        (500, "backendError", True),
        (503, "", True),
    ],
)
def test_error_classification(status, reason, retryable):
    error = youtube_api._error_from(_error(status, reason))
    assert error.retryable is retryable


def test_error_from_non_json_body_is_still_safe():
    error = youtube_api._error_from(httpx.Response(502, text="<html>bad gateway</html>"))
    assert error.retryable is True and "<html>" not in str(error)


def test_empty_video_file_is_refused(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(PublishError) as caught:
        youtube_api.upload_video(empty, "tok", {}, "video/mp4")
    assert caught.value.retryable is False


def test_chunk_size_is_a_multiple_of_256_kib():
    assert youtube_api.CHUNK_SIZE % youtube_api.CHUNK_UNIT == 0


def test_build_metadata_leaves_out_empty_tags():
    meta = youtube_api.build_metadata("T", "D", [], "private")
    assert "tags" not in meta["snippet"]
    assert meta["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}


# ----------------------------------------------------------- cron URL


@pytest.fixture()
def cron_client(monkeypatch):
    monkeypatch.setattr(cron, "get_settings", lambda: SimpleNamespace(CRON_TOKEN="cron-secret-123"))
    ran = []
    monkeypatch.setattr(worker, "run_once", lambda *a, **k: ran.append(1) or worker.RunSummary())
    with TestClient(app) as client:
        client.ran = ran
        yield client


def test_cron_is_off_without_a_token(monkeypatch):
    monkeypatch.setattr(cron, "get_settings", lambda: SimpleNamespace(CRON_TOKEN=""))
    with TestClient(app) as client:
        assert client.post("/cron/publish").status_code == 503
        assert client.post("/cron/publish", headers={"X-Cron-Token": ""}).status_code == 503


def test_cron_rejects_missing_or_wrong_token(cron_client):
    assert cron_client.post("/cron/publish").status_code == 403
    assert cron_client.post("/cron/publish", headers={"X-Cron-Token": "nope"}).status_code == 403
    assert cron_client.get("/cron/publish?token=nope").status_code == 403
    assert cron_client.ran == []


def test_cron_runs_the_worker_with_header_or_query_token(cron_client):
    r = cron_client.post("/cron/publish", headers={"X-Cron-Token": "cron-secret-123"})
    assert r.status_code == 202 and r.json() == {"status": "started"}
    r = cron_client.get("/cron/publish", params={"token": "cron-secret-123"})
    assert r.status_code == 202
    assert len(cron_client.ran) == 2


def test_cron_skips_when_a_pass_is_already_running(cron_client):
    assert cron._run_lock.acquire(blocking=False)
    try:
        r = cron_client.post("/cron/publish", headers={"X-Cron-Token": "cron-secret-123"})
        assert r.status_code == 202 and r.json() == {"status": "busy"}
        assert cron_client.ran == []
    finally:
        cron._run_lock.release()


def test_cron_survives_a_crashing_pass(monkeypatch):
    monkeypatch.setattr(cron, "get_settings", lambda: SimpleNamespace(CRON_TOKEN="t"))

    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "run_once", crash)
    with TestClient(app) as client:
        assert client.post("/cron/publish", headers={"X-Cron-Token": "t"}).status_code == 202
        assert not cron._run_lock.locked()  # the lock was released


# ------------------------------------------------------ database upgrade


def test_ensure_columns_upgrades_an_old_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    db_module.Base.metadata.create_all(engine)
    with engine.begin() as connection:  # make it look like a Phase 6 database
        connection.execute(text("ALTER TABLE schedules DROP COLUMN upload_session_url"))
        connection.execute(text("ALTER TABLE schedules DROP COLUMN claimed_at"))
    old_columns = {c["name"] for c in inspect(engine).get_columns("schedules")}
    assert "claimed_at" not in old_columns

    monkeypatch.setattr(db_module, "engine", engine)
    db_module.ensure_columns()
    db_module.ensure_columns()  # running it twice is harmless

    new_columns = {c["name"] for c in inspect(engine).get_columns("schedules")}
    assert {"upload_session_url", "claimed_at"} <= new_columns


# ------------------------------------------ pages that show the results


def _logged_in_client():
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    email = f"ui-{uuid.uuid4().hex[:10]}@example.com"
    assert client.post(
        "/register",
        data={"email": email, "password": "correct horse", "confirm_password": "correct horse"},
    ).status_code == 303
    with SessionLocal() as db:
        client.user_id = db.scalar(select(User.id).where(User.email == email))
    return client


def test_schedule_list_shows_publish_result_and_next_retry(env):
    client = _logged_in_client()
    try:
        done = make_post(env, status=STATUS_PUBLISHED, youtube_video_id="ABC123")
        waiting = make_post(env, attempts=2, next_retry_at=utcnow() + timedelta(minutes=10))
        with SessionLocal() as db:
            db.execute(
                update(Schedule)
                .where(Schedule.id == done)
                .values(
                    user_id=client.user_id, published_at=utcnow(),
                    youtube_url="https://www.youtube.com/watch?v=ABC123",
                )
            )
            db.execute(
                update(Schedule)
                .where(Schedule.id == waiting)
                .values(user_id=client.user_id, error_message="Attempt 2 of 5 failed: YouTube had a temporary problem (503).")
            )
            db.commit()
        page = client.get("/schedules").text
        assert "Watch on YouTube" in page and "watch?v=ABC123" in page
        assert "Published " in page
        assert "Next try:" in page and "Attempt 2 of 5 failed" in page
        assert "upload_id" not in page  # the resumable address is never shown
    finally:
        client.__exit__(None, None, None)


def test_editing_the_video_forgets_a_half_finished_upload(env):
    client = _logged_in_client()
    try:
        sid = make_post(env, attempts=1, session_url=f"{youtube_api.GOOGLE_UPLOAD_PREFIX}upload/x?upload_id=1")
        with SessionLocal() as db:
            schedule = db.get(Schedule, sid)
            old_video_id, account_id = schedule.video_id, schedule.youtube_account_id
            other = Video(user_id=client.user_id, storage_key=f"videos/{client.user_id}/{uuid.uuid4().hex}.mp4", title="Second video")
            account = db.get(YouTubeAccount, account_id)
            account.user_id = client.user_id
            db.add(other)
            db.flush()
            db.execute(update(Schedule).where(Schedule.id == sid).values(user_id=client.user_id))
            db.execute(update(Video).where(Video.id == old_video_id).values(user_id=client.user_id))
            db.commit()
            other_id = other.id
        when = (utcnow() + timedelta(days=2)).astimezone().strftime("%Y-%m-%dT%H:%M")

        # changing only the time keeps the saved upload
        r = client.post(
            f"/schedules/{sid}/edit",
            data={"video_id": old_video_id, "youtube_account_id": account_id, "scheduled_at": when},
        )
        assert r.status_code == 303
        assert get(sid).upload_session_url is not None

        # changing the video throws it away
        r = client.post(
            f"/schedules/{sid}/edit",
            data={"video_id": other_id, "youtube_account_id": account_id, "scheduled_at": when},
        )
        assert r.status_code == 303
        after = get(sid)
        assert after.video_id == other_id
        assert after.upload_session_url is None
    finally:
        client.__exit__(None, None, None)
