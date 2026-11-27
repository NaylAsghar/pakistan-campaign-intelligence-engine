"""Register, log in, log out, and the dependencies other routes reuse.

A session is a signed cookie (Starlette SessionMiddleware, keyed by
SECRET_KEY). After login it holds the user's id and email; logout empties
it. Later phases protect their pages with `Depends(require_user)`.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from publisher.db import get_db
from publisher.models import User
from publisher.security import (
    burn_password_check,
    hash_password,
    password_problem,
    verify_password,
)
from publisher.templating import templates

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_ERROR = "Incorrect email or password."  # same text for both failure cases


def normalize_email(raw: str) -> str:
    """Trim and lowercase so 'Ali@X.com ' and 'ali@x.com' are one account."""
    return raw.strip().lower()


def _start_session(request: Request, user: User) -> None:
    request.session.clear()  # never reuse a session from before login
    request.session["user_id"] = user.id
    request.session["email"] = user.email


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """The logged-in user, or None. Clears a cookie that points at a deleted user."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        request.session.clear()
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Use on pages that need a login; anonymous visitors go to /login."""
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def _form(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


# ---------------------------------------------------------------- register


@router.get("/register")
def register_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return _form(request, "register.html", error=None, email="")


@router.post("/register")
def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    email = normalize_email(email)

    error, status = None, 400
    if not _EMAIL_RE.match(email) or len(email) > 255:
        error = "Enter a valid email address."
    elif (problem := password_problem(password)) is not None:
        error = problem
    elif password != confirm_password:
        error = "The two passwords do not match."
    elif db.scalar(select(User).where(User.email == email)) is not None:
        error, status = "An account with this email already exists.", 409

    if error:
        return _form(request, "register.html", status, error=error, email=email)

    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two sign-ups with the same email raced; the unique index caught it.
        db.rollback()
        return _form(
            request, "register.html", 409,
            error="An account with this email already exists.", email=email,
        )

    _start_session(request, user)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------- login


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return _form(request, "login.html", error=None, email="")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email))

    if user is None:
        burn_password_check(password)
        ok = False
    else:
        ok = verify_password(password, user.password_hash)

    if not ok:
        return _form(request, "login.html", 401, error=LOGIN_ERROR, email=email)

    _start_session(request, user)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ logout


@router.post("/logout")
def logout(request: Request):
    """POST only, so a link or image on another site cannot log you out."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
