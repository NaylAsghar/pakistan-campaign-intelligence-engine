"""One shared Jinja2Templates object.

Kept in its own module so main.py and the route modules can all import it
without importing each other (which would be a circular import).

Two filters turn stored UTC times into the user's time zone (Phase 6):
    {{ schedule.scheduled_at_utc | local_dt }}      28 Nov 2026, 01:21 PM PKT
    {{ schedule.scheduled_at_utc | local_input }}   2026-11-28T13:21
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from publisher.timeutil import format_local, to_input_value

PACKAGE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
templates.env.filters["local_dt"] = format_local
templates.env.filters["local_input"] = to_input_value
