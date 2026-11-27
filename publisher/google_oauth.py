"""Talking to Google: OAuth sign-in, token refresh, and channel lookup.

Every network call goes through _post_form() or _get_json(), so tests
replace those two functions and never touch the internet.

Flow ("authorization code with PKCE"):
    1. build_auth_url()    send the user to Google
    2. Google redirects back to /youtube/callback?code=...&state=...
    3. exchange_code()     trade the code for tokens (refresh + access)
    4. fetch_channel()     ask YouTube which channel was connected
Later phases call access_token_for(account) before each YouTube API call.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from publisher.config import get_settings
from publisher.crypto import decrypt_text

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# upload: publish videos and set thumbnails. readonly: channel info and statistics.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
HTTP_TIMEOUT_SECONDS = 15


class YouTubeError(Exception):
    """A problem talking to Google. The message is safe to show to the user."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code  # Google's error code, e.g. "invalid_grant"


def is_configured() -> bool:
    s = get_settings()
    return bool(s.GOOGLE_CLIENT_ID and s.GOOGLE_CLIENT_SECRET)


# ------------------------------------------------------------ HTTP layer


def _post_form(url: str, data: dict) -> dict:
    try:
        response = httpx.post(url, data=data, timeout=HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise YouTubeError("Could not reach Google. Please try again.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        raise YouTubeError(
            body.get("error_description") or body.get("error") or
            f"Google returned an error ({response.status_code}).",
            code=body.get("error", ""),
        )
    return body


def _get_json(url: str, access_token: str, params: dict) -> dict:
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise YouTubeError("Could not reach YouTube. Please try again.") from exc
    if response.status_code >= 400:
        raise YouTubeError(f"YouTube returned an error ({response.status_code}).")
    return response.json()


# ------------------------------------------------------------ sign-in


def new_pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). The verifier stays in our session; Google gets
    only the hash, so a stolen redirect URL cannot be exchanged for tokens."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(state: str, code_challenge: str) -> str:
    """The Google page where the user approves access."""
    s = get_settings()
    query = urlencode(
        {
            "client_id": s.GOOGLE_CLIENT_ID,
            "redirect_uri": s.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",  # we need a refresh token
            "prompt": "consent",  # ...so always show the consent screen
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTH_URL}?{query}"


def exchange_code(code: str, code_verifier: str) -> dict:
    """Trade the one-time code from Google for access and refresh tokens."""
    s = get_settings()
    return _post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": s.GOOGLE_CLIENT_ID,
            "client_secret": s.GOOGLE_CLIENT_SECRET,
            "redirect_uri": s.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
    )


def fetch_channel(access_token: str) -> dict:
    """{'id': ..., 'title': ...} for the signed-in user's YouTube channel."""
    body = _get_json(CHANNELS_URL, access_token, {"part": "snippet", "mine": "true"})
    items = body.get("items") or []
    if not items:
        raise YouTubeError(
            "This Google account has no YouTube channel. "
            "Create one at youtube.com, then connect again."
        )
    return {"id": items[0]["id"], "title": items[0].get("snippet", {}).get("title", "")}


# ------------------------------------------------- later phases use these


def refresh_access_token(refresh_token: str) -> str:
    """A fresh short-lived access token. Raises YouTubeError if Google refuses
    (code 'invalid_grant' means the user must reconnect)."""
    s = get_settings()
    body = _post_form(
        TOKEN_URL,
        {
            "refresh_token": refresh_token,
            "client_id": s.GOOGLE_CLIENT_ID,
            "client_secret": s.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    if not body.get("access_token"):
        raise YouTubeError("Google did not return an access token.")
    return body["access_token"]


def access_token_for(account) -> str:
    """Decrypt a YouTubeAccount's refresh token and get an access token."""
    return refresh_access_token(decrypt_text(account.refresh_token_encrypted))


def revoke_token(token: str) -> None:
    """Tell Google to forget this grant (used on Disconnect)."""
    _post_form(REVOKE_URL, {"token": token})
