"""Connect and disconnect YouTube channels.

    GET  /youtube                    my connected channels + Connect button
    POST /youtube/connect            send the user to Google
    GET  /youtube/callback           Google sends the user back here
    POST /youtube/{id}/disconnect    revoke access and forget the channel

The refresh token is encrypted before it is stored and is never put in
a template, a log line or a message.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from publisher import google_oauth
from publisher.auth import require_user
from publisher.crypto import decrypt_text, encrypt_text
from publisher.db import get_db
from publisher.google_oauth import YouTubeError
from publisher.models import User, YouTubeAccount
from publisher.templating import templates

router = APIRouter()

_FLASH = "youtube_message"


def _flash(request: Request, kind: str, text: str) -> None:
    """One message shown on the next /youtube page view."""
    request.session[_FLASH] = {"kind": kind, "text": text}


def _back_to_page() -> RedirectResponse:
    return RedirectResponse("/youtube", status_code=303)


@router.get("/youtube")
def youtube_page(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    accounts = db.scalars(
        select(YouTubeAccount)
        .where(YouTubeAccount.user_id == user.id)
        .order_by(YouTubeAccount.id)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="youtube.html",
        context={
            "accounts": accounts,
            "message": request.session.pop(_FLASH, None),
            "configured": google_oauth.is_configured(),
        },
    )


@router.post("/youtube/connect")
def youtube_connect(request: Request, user: User = Depends(require_user)):
    if not google_oauth.is_configured():
        _flash(request, "error", "Google credentials are missing. Set GOOGLE_CLIENT_ID "
               "and GOOGLE_CLIENT_SECRET in .env and restart the app.")
        return _back_to_page()

    state = secrets.token_urlsafe(32)
    verifier, challenge = google_oauth.new_pkce_pair()
    request.session["yt_state"] = state
    request.session["yt_verifier"] = verifier
    return RedirectResponse(google_oauth.build_auth_url(state, challenge), status_code=303)


@router.get("/youtube/callback")
def youtube_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # Each sign-in attempt can be used once, so remove it from the session now.
    expected_state = request.session.pop("yt_state", "")
    verifier = request.session.pop("yt_verifier", "")

    if not expected_state or not secrets.compare_digest(
        expected_state.encode("utf-8"), state.encode("utf-8")
    ):
        _flash(request, "error", "The sign-in session expired or was not valid. "
               "Please press Connect again.")
        return _back_to_page()
    if error:
        _flash(request, "error", "Google sign-in was cancelled or access was denied.")
        return _back_to_page()
    if not code or not verifier:
        _flash(request, "error", "Google did not send a sign-in code. Please try again.")
        return _back_to_page()

    try:
        tokens = google_oauth.exchange_code(code, verifier)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise YouTubeError(
                "Google did not return a refresh token. Remove this app at "
                "myaccount.google.com/permissions and connect again."
            )
        channel = google_oauth.fetch_channel(tokens["access_token"])
        encrypted = encrypt_text(refresh_token)
    except YouTubeError as exc:
        _flash(request, "error", str(exc))
        return _back_to_page()
    except (KeyError, ValueError):
        _flash(request, "error", "Could not finish connecting. Check FERNET_KEY in .env.")
        return _back_to_page()

    account = db.scalar(
        select(YouTubeAccount).where(
            YouTubeAccount.user_id == user.id,
            YouTubeAccount.channel_id == channel["id"],
        )
    )
    if account is None:
        account = YouTubeAccount(user_id=user.id, channel_id=channel["id"])
        db.add(account)
    account.channel_title = channel["title"]
    account.refresh_token_encrypted = encrypted
    db.commit()

    _flash(request, "ok", f"Connected channel: {channel['title'] or channel['id']}")
    return _back_to_page()


@router.post("/youtube/{account_id}/disconnect")
def youtube_disconnect(
    request: Request,
    account_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    account = db.get(YouTubeAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Channel not found")

    if account.schedules:
        _flash(request, "error", "This channel has scheduled or past posts. "
               "Delete those posts before disconnecting it.")
        return _back_to_page()

    try:  # best effort: even if Google cannot be reached we still forget the token
        google_oauth.revoke_token(decrypt_text(account.refresh_token_encrypted))
    except (YouTubeError, ValueError):
        pass

    title = account.channel_title or account.channel_id
    db.delete(account)
    db.commit()
    _flash(request, "ok", f"Disconnected channel: {title}")
    return _back_to_page()
