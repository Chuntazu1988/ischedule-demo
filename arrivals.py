"""Shared, file-persisted shift-arrival log for iSchedule.

Streamlit's st.session_state is per-browser-session, not shared across
users — an employee marking arrival in their own session would never be
visible to an admin looking at a different session. This module persists
arrivals to a small JSON file on disk instead, so every session reads/writes
the same shared record (consistent with how gantt_data.xlsx already writes
the built schedule to disk as a shared artifact).

Callers pass in the "now" datetime explicitly (rather than this module
calling datetime.now() itself) so it stays consistent with the app's own
simulated dev clock (app_now() in streamlit_app.py) instead of real wall-clock
time.
"""

import json
import os

_ARRIVALS_FILE = "arrivals.json"


def _load() -> dict:
    if not os.path.exists(_ARRIVALS_FILE):
        return {}
    try:
        with open(_ARRIVALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    try:
        with open(_ARRIVALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def record_arrival(employee_name: str, now) -> str:
    """Marks `employee_name` as arrived at `now` (today's date, per `now`).
    Returns the recorded time string "HH:MM"."""
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%H:%M")
    data = _load()
    data.setdefault(today, {})[employee_name] = now_str
    _save(data)
    return now_str


def get_arrival_today(employee_name: str, now) -> str | None:
    today = now.strftime("%Y-%m-%d")
    return _load().get(today, {}).get(employee_name)


def get_all_arrivals_today(now) -> dict:
    today = now.strftime("%Y-%m-%d")
    return _load().get(today, {})


def clear_all_arrivals() -> None:
    """Called on "צור סידור חדש" — a fresh schedule day shouldn't carry over
    arrival marks from testing/a previous draft (found via real data
    2026-07-30: a stale ✅ arrival mark from an earlier test leaked into a
    freshly rebuilt, unrelated schedule)."""
    _save({})


