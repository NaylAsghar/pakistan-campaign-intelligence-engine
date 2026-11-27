"""Password hashing with bcrypt.

Only the hash is stored in users.password_hash; the plain password is
never saved or logged. bcrypt salts every hash itself, so two users with
the same password get different hashes.
"""

from __future__ import annotations

from functools import lru_cache

import bcrypt

# bcrypt only looks at the first 72 bytes of a password. Longer passwords
# are rejected up front so the user is never silently truncated.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash as text (starts with '$2b$')."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """True if `password` matches `password_hash`; False for any bad input."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:  # malformed hash or over-long password
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("not-a-real-password")


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check.

    Called when the email is unknown, so an attacker cannot tell
    "no such account" from "wrong password" by measuring response time.
    """
    verify_password(password, _dummy_hash())


def password_problem(password: str) -> str | None:
    """Return a human-readable problem with `password`, or None if it is fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes long."
    return None
