"""SQLAlchemy engine, sessions, and table creation.

DATABASE_URL selects the database: a SQLite file locally, Postgres in
production. This module is imported for the first time only after the
environment is loaded, because the engine is built immediately.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
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
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


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


def _column_names(table_name: str) -> set[str]:
    """Current column names. A fresh inspector each time: inspectors cache."""
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def ensure_columns() -> None:
    """Add columns that the code has but an older database file does not.

    create_all() only creates missing TABLES. When a new phase adds a column
    to a table that already exists (Phase 7 adds two to `schedules`), this
    runs `ALTER TABLE ... ADD COLUMN` for it. Only nullable columns are added,
    so existing rows stay valid. Works on SQLite and Postgres; a fresh
    database has nothing to add.

    If the web app and the worker start at the same moment, both may try to
    add the same column. The loser's ALTER fails; we then check the column
    exists and carry on.
    """
    existing_tables = set(inspect(engine).get_table_names())
    preparer = engine.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = _column_names(table.name)
        for column in table.columns:
            if column.name in have:
                continue
            if not column.nullable:
                raise RuntimeError(
                    f"Column {table.name}.{column.name} is new and required; "
                    "add it with a manual migration."
                )
            column_type = column.type.compile(dialect=engine.dialect)
            statement = (
                f"ALTER TABLE {preparer.quote(table.name)} "
                f"ADD COLUMN {preparer.quote(column.name)} {column_type}"
            )
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
            except (OperationalError, ProgrammingError):
                if column.name not in _column_names(table.name):
                    raise  # a real failure, not a lost race


def init_db() -> None:
    """Create any tables that are not in the database yet, then add any
    columns that an older database is missing.

    Importing models registers them on Base. The import stays inside this
    function so models.py can import Base without a circular import.
    """
    from publisher import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_columns()
