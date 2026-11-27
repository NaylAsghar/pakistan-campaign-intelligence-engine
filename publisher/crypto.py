"""Encrypt and decrypt secrets (YouTube refresh tokens) with Fernet.

FERNET_KEY comes from .env. If it is lost, every stored token becomes
unreadable and users must reconnect YouTube. Create a key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from publisher.config import get_settings


def _fernet() -> Fernet:
    try:
        return Fernet(get_settings().FERNET_KEY.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError("FERNET_KEY is not a valid Fernet key.") from exc


def encrypt_text(plain: str) -> str:
    """Return `plain` encrypted, as text that is safe to store in the database."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_text(token: str) -> str:
    """Reverse encrypt_text. Raises ValueError for tampered data or a wrong key."""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted (wrong FERNET_KEY?).") from exc
