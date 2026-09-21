"""Draft-vs-published schedule state.

A freshly built schedule is a DRAFT — visible only to admins/managers in the
control-center dashboard. Employees (viewer accounts) must not see anything
until an admin explicitly clicks "שגר סידור" (publish), at which point a
snapshot of the schedule is written to PUBLISHED_FILE and employee_view.py
starts reading from it.

File-persisted (like arrivals.py) since publish state must be visible across
every browser session, not just the admin's own st.session_state.
"""

import json
import os

import pandas as pd

PUBLISHED_FILE = "published_schedule.xlsx"
_META_FILE = "published_schedule_meta.json"


def _load_meta() -> dict:
    if not os.path.exists(_META_FILE):
        return {}
    try:
        with open(_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def publish_schedule(schedule_df, build_id: str, now) -> bool:
    """Snapshot `schedule_df` as THE published schedule. Returns True on
    success."""
    try:
        schedule_df.to_excel(PUBLISHED_FILE, index=False)
    except Exception:
        return False
    try:
        with open(_META_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"build_id": build_id, "published_at": now.strftime("%Y-%m-%d %H:%M")},
                f, ensure_ascii=False, indent=2,
            )
    except OSError:
        pass
    return True


def get_published_build_id() -> str | None:
    return _load_meta().get("build_id")


def get_published_at() -> str | None:
    return _load_meta().get("published_at")


def is_published(build_id: str | None) -> bool:
    """True when `build_id` (the CURRENT draft's build id) matches what was
    last published — i.e. the draft on screen IS what employees see."""
    if not build_id:
        return False
    return get_published_build_id() == build_id


def clear_published() -> None:
    """Called on "צור סידור חדש" — a fresh schedule day starts unpublished."""
    for f in (PUBLISHED_FILE, _META_FILE):
        try:
            os.remove(f)
        except OSError:
            pass


def get_published_schedule():
    """Returns a DataFrame, or None if nothing has been published."""
    if not os.path.exists(PUBLISHED_FILE):
        return None
    try:
        return pd.read_excel(PUBLISHED_FILE)
    except Exception:
        return None
