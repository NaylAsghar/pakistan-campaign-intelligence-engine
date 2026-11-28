"""Phase 2 tests: password hashing, register, login, logout, protected pages."""
import uuid

import pytest

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher.auth import LOGIN_ERROR, require_user
from publisher.db import SessionLocal
from publisher.main import app
from publisher.models import User
from publisher.security import hash_password, password_problem, verify_password

PASSWORD = "correct horse"


# A tiny page that needs a login, used only by these tests.
@app.get("/_test/whoami")
def _whoami(user: User = Depends(require_user)) -> dict:
    return {"email": user.email}


@pytest.fixture()
def client():
    # `with` runs the app's lifespan, which creates the tables.
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def email():
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


def register(client, email, password=PASSWORD, confirm=None):
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def user_in_db(email):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email))


# ------------------------------------------------------------ hashing


def test_hash_is_salted_and_verifies():
    h1, h2 = hash_password(PASSWORD), hash_password(PASSWORD)
    assert h1 != h2 and h1.startswith("$2")
    assert verify_password(PASSWORD, h1)
    assert not verify_password("wrong password", h1)


def test_verify_rejects_malformed_hash():
    assert verify_password(PASSWORD, "not-a-bcrypt-hash") is False


def test_password_rules():
    assert password_problem("short") is not None
    assert password_problem("a" * 73) is not None
    assert password_problem("a" * 72) is None
    assert password_problem(PASSWORD) is None


# ----------------------------------------------------------- register


def test_register_pages_render(client):
    assert client.get("/register").status_code == 200
    assert client.get("/login").status_code == 200


def test_register_creates_hashed_user_and_logs_in(client, email):
    r = register(client, email.upper())  # email is stored lowercase
    assert r.status_code == 303 and r.headers["location"] == "/"

    user = user_in_db(email)
    assert user is not None
    assert user.password_hash != PASSWORD and verify_password(PASSWORD, user.password_hash)
    assert client.get("/_test/whoami").json() == {"email": email}


def test_register_duplicate_email_rejected(client, email):
    register(client, email)
    with TestClient(app, follow_redirects=False) as other:
        r = register(other, email.upper())
    assert r.status_code == 409
    assert "already exists" in r.text


@pytest.mark.parametrize(
    "bad_email, password, confirm, message",
    [
        ("not-an-email", PASSWORD, PASSWORD, "valid email"),
        ("a@b.com", "short", "short", "at least 8"),
        ("a@b.com", PASSWORD, "different pass", "do not match"),
    ],
)
def test_register_validation(client, bad_email, password, confirm, message):
    r = register(client, bad_email, password, confirm)
    assert r.status_code == 400 and message in r.text
    assert "session" not in r.cookies


# -------------------------------------------------------------- login


def test_login_success(client, email):
    register(client, email)
    client.post("/logout")
    r = client.post("/login", data={"email": f"  {email.upper()} ", "password": PASSWORD})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/_test/whoami").status_code == 200


def test_login_failures_use_the_same_message(client, email):
    register(client, email)
    client.post("/logout")
    wrong_pw = client.post("/login", data={"email": email, "password": "nope nope nope"})
    no_user = client.post(
        "/login", data={"email": "ghost@example.com", "password": PASSWORD}
    )
    for r in (wrong_pw, no_user):
        assert r.status_code == 401 and LOGIN_ERROR in r.text
    assert client.get("/_test/whoami").status_code == 303


def test_logged_in_user_is_redirected_away_from_login(client, email):
    register(client, email)
    assert client.get("/login").headers["location"] == "/"
    assert client.get("/register").headers["location"] == "/"


# ----------------------------------------------------- logout / guard


def test_protected_page_redirects_anonymous(client):
    r = client.get("/_test/whoami")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_logout_ends_session(client, email):
    register(client, email)
    assert client.get("/_test/whoami").status_code == 200
    r = client.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/_test/whoami").status_code == 303


def test_logout_requires_post(client):
    assert client.get("/logout").status_code == 405


def test_nav_reflects_login_state(client, email):
    assert "Log in" in client.get("/").text
    register(client, email)
    page = client.get("/", follow_redirects=True).text   
    assert email in page and "Log out" in page
    