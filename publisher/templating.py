"""One shared Jinja2Templates object.

Kept in its own module so main.py and the route modules can all import it
without importing each other (which would be a circular import).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

PACKAGE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
