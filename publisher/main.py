"""FastAPI application for the product video scheduler.

From the repo root, with SECRET_KEY and FERNET_KEY set:

    uvicorn publisher.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from publisher import auth, products, schedules, uploads, videos, youtube
from publisher.config import get_settings
from publisher.db import init_db
from publisher.templating import PACKAGE_DIR, templates

SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # stay logged in for 14 days


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables when the process starts. `_app` is passed by FastAPI."""
    init_db()
    yield


settings = get_settings()

app = FastAPI(title="Product Video Scheduler", lifespan=lifespan)

# Signed (not encrypted) cookie: the user can read it but cannot change it.
# SameSite=lax stops other sites from making the browser send it on POSTs.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=settings.SESSION_COOKIE_SECURE,
)

app.mount(
    "/static",
    StaticFiles(directory=str(PACKAGE_DIR / "static")),
    name="static",
)
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(uploads.router)
app.include_router(videos.router)
app.include_router(schedules.router)
app.include_router(youtube.router)


@app.get("/health")
def health() -> dict:
    """Liveness check for tests and for the host that runs the app."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Home page. `request` is required so templates can build URLs later."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )
