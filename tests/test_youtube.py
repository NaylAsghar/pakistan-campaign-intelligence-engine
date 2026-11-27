"""Phase 4 tests: encryption, Google OAuth helpers, connect / callback / disconnect.

No test talks to Google: google_oauth._post_form and _get_json are replaced.
"""

import base64
import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher import google_oauth, youtube
from publisher.config import get_settings
from publisher.crypto import decrypt_text, encrypt_text
from publisher.db import SessionLocal
from publisher.google_oauth import YouTubeError
from publisher.main import app
from publisher.models import Schedule, User, Video, YouTubeAccount

PASSWORD = "correct horse"
CHANNEL = {"id": "UC_test_channel_1", "title": "Lawn Brand"}


@pytest.fixture(autouse=True)
def google_settings(monkeypatch):
    """Pretend Google credentials are configured."""
    real = get_settings()
    fake = replace(
        real,
        GOOGLE_CLIENT_ID="test-client-id",
        GOOGLE_CLIENT_SECRET="test-client-secret",
        GOOGLE_REDIRECT_URI="http://testserver/youtube/callback",
    )
    monkeypatch.setattr(google_oauth, "get_settings", lambda: fake)


@pytest.fixture()
def google(monkeypatch):
    """Fake Google. `google.calls` records every request; edit `google.tokens`
    or `google.channel` inside a test to change what Google 'answers'."""

    class FakeGoogle:
        def __init__(self):
            self.calls = []
            self.tokens = {"access_token": "access-1", "refresh_token": "refresh-SECRET-1"}
            self.channel_items = [{"id": CHANNEL["id"], "snippet": {"title": CHANNEL["title"]}}]
            self.post_error = None

    fake = FakeGoogle()

    def post_form(url, data):
        fake.calls.append(("POST", url, dict(data)))
        if fake.post_error:
            raise fake.post_error
        if url == google_oauth.TOKEN_URL:
            if data["grant_type"] == "refresh_token":
                return {"access_token": "fresh-access"}
            return dict(fake.tokens)
        return {}

    def get_json(url, access_token, params):
        fake.calls.append(("GET", url, {"token": access_token, **params}))
        return {"items": fake.channel_items}

    monkeypatch.setattr(google_oauth, "_post_form", post_form)
    monkeypatch.setattr(google_oauth, "_get_json", get_json)
    return fake


def new_client():
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
    with new_client() as c:
        sign_up(c)
        yield c


@pytest.fixture()
def other_client():
    with new_client() as c:
        sign_up(c)
        yield c


def start_connect(client):
    """Press Connect; return the query parameters we sent to Google."""
    r = client.post("/youtube/connect")
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    assert url.netloc == "accounts.google.com"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def finish_connect(client, google_params, code="auth-code-1"):
    return client.get(
        "/youtube/callback", params={"code": code, "state": google_params["state"]}
    )


def accounts_in_db():
    with SessionLocal() as db:
        return db.scalars(select(YouTubeAccount)).all()


def connect_channel(client):
    params = start_connect(client)
    r = finish_connect(client, params)
    assert r.status_code == 303 and r.headers["location"] == "/youtube"
    with SessionLocal() as db:
        return db.scalar(
            select(YouTubeAccount).order_by(YouTubeAccount.id.desc())
        ).id


# ------------------------------------------------------------ crypto


def test_encrypt_roundtrip_and_tamper_detection():
    token = encrypt_text("my-refresh-token")
    assert token != "my-refresh-token" and "my-refresh-token" not in token
    assert encrypt_text("my-refresh-token") != token  # random each time
    assert decrypt_text(token) == "my-refresh-token"
    with pytest.raises(ValueError):
        decrypt_text(token[:-4] + "AAAA")
    with pytest.raises(ValueError):
        decrypt_text("not a token")


# ------------------------------------------------------ oauth helpers


def test_pkce_challenge_matches_verifier():
    verifier, challenge = google_oauth.new_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected and len(verifier) >= 43


def test_auth_url_has_required_parameters():
    url = google_oauth.build_auth_url("STATE123", "CHALLENGE456")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["client_id"] == "test-client-id"
    assert q["redirect_uri"] == "http://testserver/youtube/callback"
    assert q["response_type"] == "code"
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["state"] == "STATE123"
    assert q["code_challenge"] == "CHALLENGE456" and q["code_challenge_method"] == "S256"
    assert "youtube.upload" in q["scope"] and "youtube.readonly" in q["scope"]


def test_refresh_access_token_posts_refresh_grant(google):
    assert google_oauth.refresh_access_token("rt") == "fresh-access"
    _, url, data = google.calls[-1]
    assert url == google_oauth.TOKEN_URL
    assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "rt"
    assert data["client_secret"] == "test-client-secret"


def test_access_token_for_decrypts_stored_token(google):
    class Account:
        refresh_token_encrypted = encrypt_text("stored-refresh")

    assert google_oauth.access_token_for(Account()) == "fresh-access"
    assert google.calls[-1][2]["refresh_token"] == "stored-refresh"


def test_refresh_error_is_reported(google):
    google.post_error = YouTubeError("Token has been revoked", code="invalid_grant")
    with pytest.raises(YouTubeError) as caught:
        google_oauth.refresh_access_token("rt")
    assert caught.value.code == "invalid_grant"


def test_fetch_channel_without_channel(google):
    google.channel_items = []
    with pytest.raises(YouTubeError, match="no YouTube channel"):
        google_oauth.fetch_channel("token")


# ----------------------------------------------------------- the pages


def test_pages_need_login():
    with new_client() as anon:
        for method, path in [
            ("get", "/youtube"),
            ("post", "/youtube/connect"),
            ("get", "/youtube/callback?code=x&state=y"),
            ("post", "/youtube/1/disconnect"),
        ]:
            r = getattr(anon, method)(path)
            assert r.status_code == 303 and r.headers["location"] == "/login"


def test_page_shows_connect_button(client):
    page = client.get("/youtube").text
    assert "Connect YouTube" in page and "No YouTube channel connected" in page


def test_page_warns_when_not_configured(client, monkeypatch):
    real = get_settings()
    monkeypatch.setattr(
        google_oauth, "get_settings",
        lambda: replace(real, GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""),
    )
    assert "not configured" in client.get("/youtube").text
    r = client.post("/youtube/connect")
    assert r.status_code == 303 and r.headers["location"] == "/youtube"
    assert "credentials are missing" in client.get("/youtube").text


def test_connect_redirects_to_google_with_fresh_state(client):
    first = start_connect(client)
    second = start_connect(client)
    assert first["state"] != second["state"]
    assert first["client_id"] == "test-client-id"


# ------------------------------------------------------------ callback


def test_successful_connection_stores_encrypted_token(client, google):
    params = start_connect(client)
    r = finish_connect(client, params, code="the-code")
    assert r.status_code == 303 and r.headers["location"] == "/youtube"

    # what we sent Google for the token exchange
    _, url, data = [c for c in google.calls if c[0] == "POST"][0]
    assert url == google_oauth.TOKEN_URL
    assert data["code"] == "the-code" and data["grant_type"] == "authorization_code"
    assert data["client_secret"] == "test-client-secret"
    verifier = data["code_verifier"]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == params["code_challenge"]  # PKCE pair really belongs together

    # what we stored
    (account,) = accounts_in_db()[-1:]
    assert account.channel_id == CHANNEL["id"] and account.channel_title == "Lawn Brand"
    assert account.refresh_token_encrypted != "refresh-SECRET-1"
    assert "refresh-SECRET-1" not in account.refresh_token_encrypted
    assert decrypt_text(account.refresh_token_encrypted) == "refresh-SECRET-1"

    # what the user sees
    page = client.get("/youtube").text
    assert "Lawn Brand" in page and "Connected channel" in page
    assert "refresh-SECRET-1" not in page and account.refresh_token_encrypted not in page
    assert "Disconnect" in page
    assert "Connected channel" not in client.get("/youtube").text  # message shown once


def test_reconnecting_same_channel_updates_instead_of_duplicating(client, google):
    connect_channel(client)
    google.tokens["refresh_token"] = "refresh-SECRET-2"
    google.channel_items[0]["snippet"]["title"] = "Renamed Brand"
    connect_channel(client)

    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).order_by(User.id.desc()))
        mine = db.scalars(
            select(YouTubeAccount).where(YouTubeAccount.user_id == user_id)
        ).all()
    assert len(mine) == 1
    assert mine[0].channel_title == "Renamed Brand"
    assert decrypt_text(mine[0].refresh_token_encrypted) == "refresh-SECRET-2"


def test_callback_rejects_wrong_state(client, google):
    start_connect(client)
    before = len(accounts_in_db())
    r = client.get("/youtube/callback", params={"code": "c", "state": "forged"})
    assert r.status_code == 303
    assert "expired or was not valid" in client.get("/youtube").text
    assert len(accounts_in_db()) == before
    assert not [c for c in google.calls if c[0] == "POST"]  # never called Google


def test_callback_without_starting_is_rejected(client, google):
    r = client.get("/youtube/callback", params={"code": "c", "state": "anything"})
    assert r.status_code == 303
    assert "expired or was not valid" in client.get("/youtube").text


def test_state_can_only_be_used_once(client, google):
    params = start_connect(client)
    finish_connect(client, params)
    google.calls.clear()
    finish_connect(client, params)  # replay
    assert "expired or was not valid" in client.get("/youtube").text
    assert not google.calls


def test_user_denies_access(client, google):
    params = start_connect(client)
    r = client.get(
        "/youtube/callback", params={"error": "access_denied", "state": params["state"]}
    )
    assert r.status_code == 303
    assert "cancelled or access was denied" in client.get("/youtube").text


def test_missing_refresh_token_is_an_error(client, google):
    google.tokens.pop("refresh_token")
    before = len(accounts_in_db())
    finish_connect(client, start_connect(client))
    assert "refresh token" in client.get("/youtube").text
    assert len(accounts_in_db()) == before


def test_google_failure_is_shown_not_crashed(client, google):
    google.post_error = YouTubeError("Malformed auth code.", code="invalid_grant")
    before = len(accounts_in_db())
    finish_connect(client, start_connect(client))
    assert "Malformed auth code" in client.get("/youtube").text
    assert len(accounts_in_db()) == before


def test_account_without_channel_is_an_error(client, google):
    google.channel_items = []
    before = len(accounts_in_db())
    finish_connect(client, start_connect(client))
    assert "no YouTube channel" in client.get("/youtube").text
    assert len(accounts_in_db()) == before


# ---------------------------------------------------------- disconnect


def test_disconnect_revokes_and_deletes(client, google):
    account_id = connect_channel(client)
    r = client.post(f"/youtube/{account_id}/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/youtube"

    revoke = [c for c in google.calls if c[1] == google_oauth.REVOKE_URL]
    assert revoke and revoke[0][2]["token"] == "refresh-SECRET-1"
    with SessionLocal() as db:
        assert db.get(YouTubeAccount, account_id) is None
    assert "Disconnected channel" in client.get("/youtube").text


def test_disconnect_still_works_if_google_is_unreachable(client, google):
    account_id = connect_channel(client)
    google.post_error = YouTubeError("Could not reach Google.")
    client.post(f"/youtube/{account_id}/disconnect")
    with SessionLocal() as db:
        assert db.get(YouTubeAccount, account_id) is None


def test_other_users_cannot_disconnect_my_channel(client, other_client, google):
    account_id = connect_channel(client)
    assert other_client.post(f"/youtube/{account_id}/disconnect").status_code == 404
    with SessionLocal() as db:
        assert db.get(YouTubeAccount, account_id) is not None
    assert "Lawn Brand" not in other_client.get("/youtube").text


def test_disconnect_blocked_while_posts_exist(client, google):
    account_id = connect_channel(client)
    with SessionLocal() as db:
        account = db.get(YouTubeAccount, account_id)
        video = Video(user_id=account.user_id, storage_key="videos/x.mp4", title="t")
        db.add(video)
        db.flush()
        db.add(Schedule(
            user_id=account.user_id, video_id=video.id, youtube_account_id=account.id,
            scheduled_at_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
        ))
        db.commit()

    client.post(f"/youtube/{account_id}/disconnect")
    assert "Delete those posts" in client.get("/youtube").text
    with SessionLocal() as db:
        assert db.get(YouTubeAccount, account_id) is not None
