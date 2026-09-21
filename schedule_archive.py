"""Archive of past schedules ("היסטוריה").

Every time an admin clicks "⭐ התחל סידור חדש" (start a new schedule), the
CURRENT schedule — in the exact styled format it was downloaded in (the
"דוח שיבוץ טיסות - המראות" export) — plus the shift-removal summary (who was
pulled off shift, when, and why: sick, sick in family, mid-shift removal,
understaffing, etc, from the "סיכום הורדות ממשמרת" dialog) are snapshotted
here as two SEPARATE files before the app resets, so nothing is lost and past
days stay browsable/downloadable from their own tab. File-persisted (like
arrivals.py / the published-schedule store) since this needs to be shared
across sessions, not just one browser's st.session_state.

Layout:
    schedule_archive/
        index.json                    — list of entries (metadata only,
                                         including who archived it)
        <entry_id>_schedule.xlsx      — the styled departures-report export,
                                         byte-for-byte what "הורדת דוח שיבוץ
                                         טיסות" would have produced
        <entry_id>_removals.xlsx      — שם עובד / שעת הורדה / סיבת הורדה /
                                         טיסות שהיה משובץ
"""

import json
import os

import pandas as pd

_ARCHIVE_DIR = "schedule_archive"
_INDEX_FILE = os.path.join(_ARCHIVE_DIR, "index.json")


def _load_index() -> list:
    if not os.path.exists(_INDEX_FILE):
        return []
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(entries: list) -> None:
    os.makedirs(_ARCHIVE_DIR, exist_ok=True)
    try:
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def archive_schedule(
    schedule_report_bytes: bytes | None,
    now,
    archived_by: str = "",
    rows: int = 0,
    removal_summary: dict | None = None,
    segments: list | None = None,
) -> str | None:
    """Snapshot the schedule into the archive, tagged with today's date (per
    `now`, the app's own simulated clock) and `archived_by` (the username who
    clicked "התחל סידור חדש"). `schedule_report_bytes` is the ALREADY-
    RENDERED styled export (from to_departures_report_excel_bytes) — this
    module doesn't build it itself, so the archived file is guaranteed
    byte-identical to what a manual download would have produced.
    `removal_summary`, if given, is {"removed": {name: reason}, "times":
    {name: "HH:MM"}, "tasks": {name: [flight, ...]}} — the same data the
    "סיכום הורדות ממשמרת" dialog reads from st.session_state.

    `segments`, if given, is [{"key", "label", "range", "bytes"}, ...] — one
    per segment build of the day (נייט / דיי / אפטר). Each is stored as its own
    file alongside the final schedule, so the history tab can offer all three
    versions of the day that just ended, not only the last state it was left in.

    Returns the new entry_id, or None if there's nothing worth archiving."""
    if not schedule_report_bytes:
        return None

    os.makedirs(_ARCHIVE_DIR, exist_ok=True)
    entry_id = now.strftime("%Y-%m-%d_%H%M%S")

    schedule_path = os.path.join(_ARCHIVE_DIR, f"{entry_id}_schedule.xlsx")
    try:
        with open(schedule_path, "wb") as f:
            f.write(schedule_report_bytes)
    except OSError:
        return None

    removed = (removal_summary or {}).get("removed", {})
    times = (removal_summary or {}).get("times", {})
    tasks = (removal_summary or {}).get("tasks", {})
    removal_df = pd.DataFrame([
        {
            "שם עובד": name,
            "שעת הורדה": times.get(name, ""),
            "סיבת הורדה": reason,
            "טיסות שהיה משובץ": " | ".join(tasks.get(name, [])),
        }
        for name, reason in removed.items()
    ])
    removal_path = os.path.join(_ARCHIVE_DIR, f"{entry_id}_removals.xlsx")
    try:
        removal_df.to_excel(removal_path, index=False, sheet_name="סיכום הורדות")
    except Exception:
        pass

    segment_meta = []
    for _seg in (segments or []):
        _bytes = _seg.get("bytes")
        if not _bytes:
            continue
        _fname = f"{entry_id}_segment_{_seg.get('key', 'seg')}.xlsx"
        try:
            with open(os.path.join(_ARCHIVE_DIR, _fname), "wb") as f:
                f.write(_bytes)
        except OSError:
            continue
        segment_meta.append({
            "key": _seg.get("key", ""),
            "label": _seg.get("label", ""),
            "range": _seg.get("range", ""),
            "file": _fname,
        })

    entries = _load_index()
    entries.append({
        "id": entry_id,
        "segments": segment_meta,
        "date": now.strftime("%Y-%m-%d"),
        "archived_at": now.strftime("%H:%M"),
        "archived_by": archived_by,
        "rows": int(rows),
        "removed_count": int(len(removal_df)),
        "schedule_file": f"{entry_id}_schedule.xlsx",
        "removal_file": f"{entry_id}_removals.xlsx",
    })
    _save_index(entries)
    return entry_id


def list_archived_schedules() -> list:
    """Newest first."""
    return sorted(_load_index(), key=lambda e: e.get("id", ""), reverse=True)


def _entry(entry_id: str) -> dict | None:
    return next((e for e in _load_index() if e.get("id") == entry_id), None)


def _read_bytes(rel_path: str | None) -> bytes | None:
    if not rel_path:
        return None
    file_path = os.path.join(_ARCHIVE_DIR, rel_path)
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError:
        return None


def load_archived_segment_bytes(entry_id: str, segment_key: str) -> bytes | None:
    """One segment version (נייט / דיי / אפטר) of an archived day."""
    entry = _entry(entry_id)
    if entry is None:
        return None
    for seg in entry.get("segments", []):
        if seg.get("key") == segment_key:
            return _read_bytes(seg.get("file"))
    return None


def load_archived_schedule_bytes(entry_id: str) -> bytes | None:
    entry = _entry(entry_id)
    if entry is None:
        return None
    return _read_bytes(entry.get("schedule_file") or entry.get("file"))  # "file" = legacy single-sheet archives


def load_archived_removal_bytes(entry_id: str) -> bytes | None:
    entry = _entry(entry_id)
    if entry is None:
        return None
    return _read_bytes(entry.get("removal_file"))


def delete_archived_schedule(entry_id: str) -> bool:
    """Removes an entry (both files + its index row). Caller is responsible
    for the admin-only permission check — this module has no auth concept of
    its own. Returns True if an entry was found and removed."""
    entries = _load_index()
    entry = next((e for e in entries if e.get("id") == entry_id), None)
    if entry is None:
        return False
    _files = [entry.get(k) for k in ("schedule_file", "removal_file", "file")]
    _files += [s.get("file") for s in entry.get("segments", [])]
    for _rel in _files:
        if _rel:
            try:
                os.remove(os.path.join(_ARCHIVE_DIR, _rel))
            except OSError:
                pass
    _save_index([e for e in entries if e.get("id") != entry_id])
    return True
