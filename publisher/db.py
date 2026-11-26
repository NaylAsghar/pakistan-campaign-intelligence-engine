"""SQLAlchemy engine, sessions, and table creation.

DATABASE_URL selects the database: a SQLite file locally, Postgres in
production. This module is imported for the first time only after the
environment is loaded, because the engine is built immediately.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from publisher.config import get_settings


class Base(DeclarativeBase):
    """Shared base class. Every model in models.py inherits from this."""


def _engine():
    """Build an engine for whatever DATABASE_URL points at."""
    database_url = get_settings().DATABASE_URL
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        # A request may run on a different thread from the one that opened
        # the connection. SQLite rejects that unless this check is off.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args)


engine = _engine()

# SessionLocal() starts one database conversation.
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create any tables that are not in the database yet.

    Importing models registers them on Base. The import stays inside this
    function so models.py can import Base without a circular import.
    """
    from publisher import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
