"""Read every setting from the environment (and an optional .env file).

Other modules call get_settings() instead of using os.environ directly,
so secrets never have to be hard-coded. The result is cached: the file
is read once per process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


class SettingsError(RuntimeError):
    """Raised when a required variable such as SECRET_KEY is missing."""


def _read(name: str, default: str | None = None) -> str:
    """Return one environment variable.

    `default=None` means the variable is required. An empty value counts
    as missing, and the error message includes the variable name.
    """
    value = os.getenv(name)
    if value is None or value.strip() == "":
        if default is None:
            raise SettingsError(
                f"{name} is required but not set. "
                f"Add {name}=... to your .env file or to the environment."
            )
        return default
    return value.strip()


@dataclass(frozen=True)
class Settings:
    """Connection strings and secrets for one running process.

    Frozen so application code cannot overwrite a secret by accident.
    Attribute names match the environment variable names on purpose.
    """

    DATABASE_URL: str
    SECRET_KEY: str
    FERNET_KEY: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    STORAGE_BACKEND: str
    LOCAL_STORAGE_DIR: str
    S3_ENDPOINT_URL: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_BUCKET: str
    S3_REGION: str
    CRON_TOKEN: str
    APP_TIMEZONE: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and reuse them.

    load_dotenv() fills in variables from a .env file but does not
    replace variables that are already set (tests rely on that).
    """
    load_dotenv()
    return Settings(
        DATABASE_URL=_read("DATABASE_URL", "sqlite:///./publisher.db"),
        SECRET_KEY=_read("SECRET_KEY"),
        FERNET_KEY=_read("FERNET_KEY"),
        GOOGLE_CLIENT_ID=_read("GOOGLE_CLIENT_ID", ""),
        GOOGLE_CLIENT_SECRET=_read("GOOGLE_CLIENT_SECRET", ""),
        GOOGLE_REDIRECT_URI=_read(
            "GOOGLE_REDIRECT_URI", "http://localhost:8000/youtube/callback"
        ),
        STORAGE_BACKEND=_read("STORAGE_BACKEND", "local"),
        LOCAL_STORAGE_DIR=_read("LOCAL_STORAGE_DIR", "./storage"),
        S3_ENDPOINT_URL=_read("S3_ENDPOINT_URL", ""),
        S3_ACCESS_KEY=_read("S3_ACCESS_KEY", ""),
        S3_SECRET_KEY=_read("S3_SECRET_KEY", ""),
        S3_BUCKET=_read("S3_BUCKET", ""),
        S3_REGION=_read("S3_REGION", ""),
        CRON_TOKEN=_read("CRON_TOKEN", ""),
        APP_TIMEZONE=_read("APP_TIMEZONE", "Asia/Karachi"),
    )
