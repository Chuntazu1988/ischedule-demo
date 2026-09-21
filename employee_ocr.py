"""Best-effort OCR extraction of a חניך/חונך pairing table from a photo or
screenshot — for the "🧑‍🎓 הוספת טריינים חדשים" bulk-import flow in
employee_management.py, as an alternative to typing/uploading an Excel
file when the source is a printed/scanned "צימודים של"ן" sheet.

Uses Tesseract OCR (via pytesseract) with the Hebrew language pack — a
separate binary, NOT installable via pip on Windows (see
TESSERACT_INSTALL_HELP below). OCR on a Hebrew table is never perfectly
reliable, so this only ever returns a best-guess DataFrame for the admin
to review and correct in the UI — it must never be wired to write
straight into the employee DB unreviewed.
"""

import io
import os
import re
import shutil

import pandas as pd

# A cell matching this (mostly digits, with the usual phone punctuation)
# marks its whole column as a phone-number column to drop — the admin
# only ever wants the two name columns (user rule 2026-09-15: "אין צורך
# להציג את עמודת הטלפון").
_PHONE_RE = re.compile(r"^[\d\-+()\s]{6,}$")

# Known header/title words from the real "צימודים של\"ן" sheet — a row
# made up ONLY of these (plus blanks) is the header row, not data, and
# gets dropped (user rule 2026-09-15: "למחוק את הכותרות ...ואת המילים
# חונך וחניך. יש להשאיר רק שמות"). Matched after stripping punctuation/
# digits so "חניך טלפון", 'של"ן', a date like "03/26" etc. all hit.
_HEADER_WORDS = {"חונך", "חניך", "טלפון", "מס", "של\"ן", "שם", "צימודים"}

# The Windows installer commonly leaves tesseract.exe off PATH entirely —
# fall back to its own default install location before giving up (found
# via real setup 2026-09-15: `where tesseract` failed even though it was
# correctly installed with the Hebrew pack at the path below).
_WINDOWS_DEFAULT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _configure_tesseract_cmd() -> None:
    import pytesseract
    if shutil.which("tesseract"):
        return
    if os.path.exists(_WINDOWS_DEFAULT_PATH):
        pytesseract.pytesseract.tesseract_cmd = _WINDOWS_DEFAULT_PATH

TESSERACT_INSTALL_HELP = (
    "מנוע ה-OCR (Tesseract) לא מותקן במחשב הזה. יש להוריד ולהתקין מ-"
    "https://github.com/UB-Mannheim/tesseract/wiki (גרסת Windows), "
    'ולוודא שמסמנים "Hebrew" ברשימת חבילות השפה הנוספות בזמן ההתקנה.'
)

# Words within this many pixels of each other vertically are treated as
# the same table row — tuned for a typical phone-photo/screenshot
# resolution; a very high- or low-res source may need this adjusted.
_ROW_TOLERANCE_PX = 12
# A horizontal gap wider than this between two consecutive words (within
# the same row) is treated as a column boundary.
_COLUMN_GAP_PX = 40


def tesseract_available() -> bool:
    try:
        import pytesseract
        _configure_tesseract_cmd()
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _group_words_into_rows(words: list[dict]) -> list[list[dict]]:
    """words: [{"text","left","top","width","height"}, ...]. Returns rows
    sorted top-to-bottom, each a list of words sorted right-to-left (the
    correct reading order for an RTL Hebrew table's columns)."""
    if not words:
        return []
    _sorted = sorted(words, key=lambda w: w["top"])
    rows: list[list[dict]] = []
    for w in _sorted:
        _center = w["top"] + w["height"] / 2
        placed = False
        for row in rows:
            _row_center = sum(r["top"] + r["height"] / 2 for r in row) / len(row)
            if abs(_center - _row_center) <= _ROW_TOLERANCE_PX:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    rows.sort(key=lambda row: sum(r["top"] for r in row) / len(row))
    for row in rows:
        # Right-to-left: largest `left` (rightmost in the image) first —
        # the correct reading-order start for an RTL table's columns.
        row.sort(key=lambda w: -w["left"])
    return rows


def _split_row_into_cells(row: list[dict]) -> list[str]:
    """row is already sorted right-to-left. Splits on any horizontal gap
    wider than _COLUMN_GAP_PX into separate cells, joining the words
    within each cell back into left-to-right reading order (a single
    Hebrew phrase's own word order, unlike the column order above)."""
    if not row:
        return []
    cells: list[list[dict]] = [[row[0]]]
    for prev, cur in zip(row, row[1:]):
        _gap = prev["left"] - (cur["left"] + cur["width"])
        if _gap > _COLUMN_GAP_PX:
            cells.append([])
        cells[-1].append(cur)
    return [
        " ".join(w["text"] for w in sorted(cell, key=lambda w: w["left"]))
        for cell in cells
    ]


def _is_header_or_title_row(cells: list[str]) -> bool:
    _non_empty = [c.strip() for c in cells if c.strip()]
    if not _non_empty:
        return True
    # A real data row always has at least a חניך AND a חונך cell filled —
    # a row with only one non-empty cell is a merged-cell title line
    # (e.g. "03/26 צימודים של\"ן" spanning the whole row).
    if len(_non_empty) == 1:
        return True
    for cell in _non_empty:
        _stripped = re.sub(r"[\d/.\-]", "", cell).strip()
        if _stripped and _stripped not in _HEADER_WORDS:
            return False
    return True


def _drop_phone_columns(cell_rows: list[list[str]]) -> list[list[str]]:
    if not cell_rows:
        return cell_rows
    max_cols = max(len(r) for r in cell_rows)
    _drop_cols = set()
    for j in range(max_cols):
        _col_vals = [r[j] for r in cell_rows if j < len(r) and r[j].strip()]
        if not _col_vals:
            continue
        _phone_like = sum(1 for v in _col_vals if _PHONE_RE.match(v.strip()))
        if _phone_like / len(_col_vals) >= 0.6:
            _drop_cols.add(j)
    if not _drop_cols:
        return cell_rows
    return [[v for j, v in enumerate(r) if j not in _drop_cols] for r in cell_rows]


def extract_table_from_image(image_bytes: bytes) -> pd.DataFrame:
    """Returns a best-guess DataFrame, one row per detected table row, one
    column per detected cell-column (columns are NOT auto-labeled — the
    caller/UI must let the admin assign which column is חניך vs חונך,
    since column count/order can vary between photos). Raises RuntimeError
    with a user-facing Hebrew message if Tesseract isn't installed or the
    image can't be read."""
    if not tesseract_available():
        raise RuntimeError(TESSERACT_INSTALL_HELP)

    import pytesseract
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise RuntimeError(f"לא ניתן לקרוא את התמונה: {exc}") from exc

    data = pytesseract.image_to_data(
        img, lang="heb+eng", output_type=pytesseract.Output.DICT
    )
    words = [
        {
            "text": data["text"][i].strip(),
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
        }
        for i in range(len(data["text"]))
        if data["text"][i].strip() and int(data.get("conf", ["0"] * len(data["text"]))[i] or -1) >= 0
    ]
    rows = _group_words_into_rows(words)
    cell_rows = [_split_row_into_cells(row) for row in rows]
    cell_rows = _drop_phone_columns(cell_rows)
    cell_rows = [r for r in cell_rows if not _is_header_or_title_row(r)]
    max_cols = max((len(r) for r in cell_rows), default=0)
    padded = [r + [""] * (max_cols - len(r)) for r in cell_rows]
    return pd.DataFrame(padded, columns=[f"עמודה {i + 1}" for i in range(max_cols)])
