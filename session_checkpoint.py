"""Persist the in-progress schedule session across browser/server restarts.

Before this module existed, EVERY piece of the current working state — the
uploaded files, the built schedule, edits, shift-manager notes, everything —
lived only in `st.session_state`, which is pure in-memory state tied to one
browser session. Closing the site and reopening it (a real restart, not just
a rerun) started a brand-new empty session with no way to recover anything
that hadn't already gone through "⭐ התחל סידור חדש" into schedule_archive.py.
This gave the user's WORK-IN-PROGRESS a very different (and much worse)
durability guarantee than a finished, archived day — found via user report
2026-09-14: a whole 04.09 review session vanished on a simple browser reopen.

Design: a single SQLite row holds a pickled snapshot of every session_state
key that matters for resuming (see CHECKPOINT_KEYS below), plus the three
uploaded-file objects (re-wrapped as plain name+bytes, since Streamlit's own
UploadedFile isn't guaranteed picklable/stable across a process restart).
Saved after every full rerun once a schedule exists (cheap — a few hundred KB
at most) so it always reflects "the last meaningful action", matching the
same click-driven granularity schedule_archive.py already uses for the
finished-day archive. Cleared the moment "⭐ התחל סידור חדש" successfully
archives the day, so a fresh app load after that shows the normal empty
upload screen — exactly mirroring the existing archive's own reset trigger,
not a separate timer or button.
"""

import io
import os
import pickle
import sqlite3
import time

_DB_PATH = "session_checkpoint.sqlite3"

# The exact keys schedule_archive's own "start a new schedule" reset already
# treats as "current session state" (streamlit_app.py's reset list), plus the
# few extra keys (_shift_editor_sticky, flights_snap) needed to fully resume
# rendering without re-deriving anything.
CHECKPOINT_KEYS = [
    "schedule_df", "flights_snap", "employees_snap", "labeled_df",
    "workload_df", "continuity_df", "output_df", "build_seconds",
    "schedule_hours_label", "_build_id", "saved_flight_edits",
    "fids_applied", "fids_file1_bytes", "fids_file1_name",
    "fids_file2_bytes", "fids_file2_name",
    "_daily_file_name", "_t1_file_name", "_emp_file_name",
    "show_results", "show_gantt_page", "active_main_tab",
    "removed_employees", "_removal_times", "_removal_tasks",
    "_segments_built", "_segment_built_until", "show_segment_form",
    "_segment_snapshots", "_segment_current", "_view_segment",
    "_shift_editor_sticky", "_manual_emp_edits",
]

# The three uploaded files, stored as plain (name, bytes) tuples under their
# own session_state key so a restored session can rebuild a file-like object
# without depending on Streamlit's UploadedFile internals.
_FILE_OBJ_KEYS = {
    "daily_file_obj": "_daily_file_name",
    "t1_file_obj": "_t1_file_name",
    "employees_file_obj": "_emp_file_name",
}


class RestoredUploadFile(io.BytesIO):
    """Drop-in for Streamlit's UploadedFile, backed by a real io.BytesIO (the
    same pattern streamlit_app.py's own _NamedBytesIO and the scratchpad
    build scripts already use to re-wrap raw bytes) — a hand-rolled
    read/seek-only stand-in isn't accepted by openpyxl/pandas the way a real
    io.IOBase subclass is (found while testing: build_shift_map_from_excel
    silently returned zero entries against a minimal duck-typed object, but
    works against this)."""

    def __init__(self, name, data):
        super().__init__(data)
        self.name = name


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS checkpoint ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "saved_at REAL, "
        "payload BLOB)"
    )
    return conn


def save_checkpoint(session_state) -> None:
    """Snapshot the current in-progress session. Best-effort — a failure here
    (e.g. a transient lock, an unpicklable widget value slipping into one of
    the tracked keys) must never break the app itself, so every error is
    swallowed."""
    try:
        payload = {}
        for key in CHECKPOINT_KEYS:
            if key in session_state:
                payload[key] = session_state[key]
        for obj_key, name_key in _FILE_OBJ_KEYS.items():
            _obj = session_state.get(obj_key)
            if _obj is not None:
                try:
                    _obj.seek(0)
                    _data = _obj.read()
                    _obj.seek(0)
                except Exception:
                    continue
                payload[obj_key] = {
                    "name": getattr(_obj, "name", session_state.get(name_key, "")),
                    "data": _data,
                }
        if not payload:
            return
        blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO checkpoint (id, saved_at, payload) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET saved_at=excluded.saved_at, payload=excluded.payload",
                (time.time(), sqlite3.Binary(blob)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def load_checkpoint() -> dict | None:
    """Return the saved payload dict, or None if there's nothing to restore
    (or the DB doesn't exist yet / the file was ever cleared)."""
    if not os.path.exists(_DB_PATH):
        return None
    try:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload FROM checkpoint WHERE id = 1").fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return pickle.loads(row[0])
    except Exception:
        return None


def clear_checkpoint() -> None:
    try:
        conn = _connect()
        try:
            conn.execute("DELETE FROM checkpoint WHERE id = 1")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def restore_into_session_state(session_state, payload: dict) -> None:
    """Populate session_state from a loaded checkpoint payload, rebuilding
    the uploaded-file objects as RestoredUploadFile instances."""
    for key, value in payload.items():
        if key in _FILE_OBJ_KEYS:
            session_state[key] = RestoredUploadFile(value["name"], value["data"])
        else:
            session_state[key] = value
