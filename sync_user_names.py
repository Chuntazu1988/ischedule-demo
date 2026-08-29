"""One-off: re-syncs the "name" / "employee_name" fields of existing accounts
in users.json against the CURRENT employees_clean.xlsx.

Why this is needed: employees_clean.xlsx was hand-edited (user rule
2026-08-02) so every employee's personal (first) name now comes FIRST — the
367 existing accounts in users.json were generated earlier from the OLD
spelling (some surname-first), so their stored display names are now stale
relative to the qualifications file. This script does NOT touch usernames,
passwords, or roles — it only updates the display-name text, matched
order-independently (canonical_name_key, same approach already used by
generate_employee_accounts.py) so a word-order difference between the old and
new spelling doesn't block the match.

Run once:
    python sync_user_names.py
"""

import json

import pandas as pd

import auth
from utils.helpers import name_key

EMPLOYEES_FILE = "employees_clean (1).xlsx"


def word_set(name: str) -> frozenset:
    return frozenset(name_key(w) for w in str(name).split() if len(w) > 1)


def main():
    users = auth._load_users()

    df = pd.read_excel(EMPLOYEES_FILE)
    if "פעיל/לא פעיל" in df.columns:
        df = df[df["פעיל/לא פעיל"].astype(str).str.strip() != "לא"]
    current_names = [str(n).strip() for n in df["שם"].tolist() if str(n).strip()]
    current_sets = [(n, word_set(n)) for n in current_names]

    updated, unmatched = [], []
    for uname, entry in users.items():
        old_display = entry.get("name", "")
        old_emp = entry.get("employee_name", "") or old_display
        if not old_emp:
            continue
        old_set = word_set(old_emp)

        # Exact match first; else a SINGLE subset match either direction —
        # the file may now carry an extra given/middle/surname component the
        # account's stored name doesn't (or vice versa). Anchoring on subset
        # (not raw word overlap) avoids the known "shares a surname with a
        # different person" collision (same approach already used in
        # data_loader.py's apply_shift_map_to_employees). If more than one
        # candidate subset-matches, it's genuinely ambiguous — leave it for
        # manual review rather than guess.
        exact = [n for n, s in current_sets if s == old_set]
        if exact:
            new_name = exact[0]
        else:
            subset = [n for n, s in current_sets if old_set <= s or s <= old_set]
            new_name = subset[0] if len(subset) == 1 else None

        if new_name is None:
            unmatched.append((uname, old_emp))
            continue
        if new_name != old_emp or new_name != old_display:
            entry["name"] = new_name
            if entry.get("employee_name"):
                entry["employee_name"] = new_name
            updated.append((uname, old_emp, new_name))

    auth._save_users(users)

    with open("sync_user_names_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Updated {len(updated)} account(s):\n")
        for uname, old, new in updated:
            f.write(f"  {uname}: {old!r} -> {new!r}\n")
        f.write(f"\nUnmatched {len(unmatched)} account(s) (left untouched):\n")
        for uname, old in unmatched:
            f.write(f"  {uname}: {old!r}\n")

    print(f"Updated {len(updated)}, unmatched {len(unmatched)} - see sync_user_names_report.txt")


if __name__ == "__main__":
    main()
