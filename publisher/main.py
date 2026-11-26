"""FastAPI application for the product video scheduler.

From the repo root, with SECRET_KEY and FERNET_KEY set:

    uvicorn publisher.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from publisher.db import init_db

# This file lives in publisher/, next to templates/ and static/.
PACKAGE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables when the process starts. `_app` is passed by FastAPI."""
    init_db()
    yield


app = FastAPI(title="Product Video Scheduler", lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=str(PACKAGE_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


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
