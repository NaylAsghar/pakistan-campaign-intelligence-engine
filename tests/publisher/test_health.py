"""The app answers /health, renders the home page, and creates its tables."""

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from publisher.db import Base, engine, init_db
from publisher.main import app

EXPECTED_TABLES = {
    "users",
    "products",
    "product_images",
    "youtube_accounts",
    "videos",
    "schedules",
    "video_stats",
}


def test_health_returns_ok():
    # Entering the client runs the startup hook, which calls init_db().
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_renders():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Product Video Scheduler" in response.text


def test_tables_can_be_created():
    init_db()

    # The model list and the live database should contain the same tables.
    assert EXPECTED_TABLES == set(Base.metadata.tables)
    created = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= created
