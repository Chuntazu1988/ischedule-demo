"""One-off / re-runnable script: creates a "viewer" (personal-screen) account
for every ACTIVE employee in employees_clean.xlsx, writing them into
users.json (the live app's credential store — see auth.py) and printing a
plaintext handout of username/password pairs for the admin to distribute.

Run it whenever new employees are added:
    python generate_employee_accounts.py

Username scheme (user rule 2026-07-26): an English, transliterated username,
NOT the Hebrew name itself. Transliteration is approximate (Hebrew spelling
often omits vowels) — good enough for a unique, readable login, not an
official/legal spelling. For each employee up to 3 candidate usernames are
considered (in order); the first not already taken (by an existing account
or another employee processed earlier in this run) is the one actually
stored — the others are listed in the CSV for admin visibility only.

Passwords are randomly generated per employee (never shared/reused) and
every new account is created with must_change_password=True, so the
employee is forced to pick their own password on first login (auth.py's
render_login_gate handles the forced change).

SECURITY: initial_employee_credentials.csv contains PLAINTEXT passwords —
it exists only so you can distribute them once. Delete it (or at least move
it somewhere secure) after handing out credentials; it's gitignored but
still sits in plain text on disk until you remove it.
"""

import csv
import json
import os
import secrets
import string

import pandas as pd

import auth  # reuses hash_password / USERS_FILE / _load_users-equivalent logic
from utils.helpers import name_key

EMPLOYEES_FILE = "employees_clean (1).xlsx"
CREDENTIALS_CSV = "initial_employee_credentials.csv"


def canonical_name_key(name: str) -> str:
    """Order-invariant identity for a person's name — this employee data has
    a known recurring issue where the SAME person appears more than once
    with their name's word order swapped (given-name first vs surname first),
    which is exactly why name_key/name_key_reversed matching already exists
    throughout the scheduler. A plain per-row name_key() doesn't catch this
    (order still matters), so this sorts the individually-normalized words
    before joining, making word order irrelevant (found via real data
    2026-07-26: the bulk account generator created TWO separate accounts for
    the same employee under each name-order variant)."""
    return "".join(sorted(name_key(w) for w in str(name).split()))

# Approximate Hebrew -> Latin transliteration. Not linguistically exact
# (Hebrew commonly omits vowels in writing) — chosen for readability and
# uniqueness as a SYSTEM-GENERATED username, not an official spelling.
_HEB_MAP = {
    "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "o", "ז": "z",
    "ח": "ch", "ט": "t", "י": "i", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "", "פ": "p", "ף": "f",
    "צ": "tz", "ץ": "tz", "ק": "k", "ר": "r", "ש": "sh", "ת": "t",
    "'": "", '"': "", "-": "", "׳": "", "״": "",
}


def transliterate_word(word: str) -> str:
    out = "".join(_HEB_MAP.get(ch, "") for ch in word)
    return out or "x"


def candidate_usernames(full_name: str) -> list[str]:
    words = [transliterate_word(w) for w in full_name.split() if w.strip()]
    if not words:
        return ["employee"]
    joined_dot = ".".join(words)
    joined_flat = "".join(words)
    initials_last = "".join(w[0] for w in words[:-1]) + words[-1] if len(words) > 1 else joined_flat
    # De-dupe while preserving order.
    seen, out = set(), []
    for cand in (joined_dot, joined_flat, initials_last):
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def random_password(length: int = 10) -> str:
    # Avoid visually-ambiguous characters (0/O, 1/l/I) for easier manual entry.
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    df = pd.read_excel(EMPLOYEES_FILE)
    if "פעיל/לא פעיל" in df.columns:
        df = df[df["פעיל/לא פעיל"].astype(str).str.strip() == "כן"]
    _raw_names = [str(n).strip() for n in df["שם"].tolist() if str(n).strip()]

    # De-dupe same-person rows whose name's word order differs (see
    # canonical_name_key) — keep the FIRST spelling seen, one account per
    # real person.
    _seen_keys = set()
    names = []
    for n in _raw_names:
        k = canonical_name_key(n)
        if k in _seen_keys:
            continue
        _seen_keys.add(k)
        names.append(n)

    users = auth._load_users()  # preserves existing admin (and seeds from
                                 # secrets.toml on first run if users.json
                                 # doesn't exist yet)
    users.pop("employee", None)  # drop the placeholder example account

    taken_usernames = set(users.keys())
    rows_for_csv = []

    for name in names:
        cands = candidate_usernames(name)
        chosen = None
        for c in cands:
            if c not in taken_usernames:
                chosen = c
                break
        if chosen is None:
            # All candidates collided — append a numeric suffix to the last one.
            base = cands[-1]
            i = 2
            while f"{base}{i}" in taken_usernames:
                i += 1
            chosen = f"{base}{i}"
        taken_usernames.add(chosen)

        pw = random_password()
        users[chosen] = {
            "name": name,
            "password_hash": auth.hash_password(pw),
            "role": "viewer",
            "employee_name": name,
            "must_change_password": True,
        }
        rows_for_csv.append({
            "employee_name": name,
            "username": chosen,
            "password": pw,
            "other_username_options_considered": ", ".join(c for c in cands if c != chosen),
        })

    with open(auth.USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    with open(CREDENTIALS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["employee_name", "username", "password", "other_username_options_considered"],
        )
        writer.writeheader()
        writer.writerows(rows_for_csv)

    print(f"Created/updated {len(rows_for_csv)} employee accounts in {auth.USERS_FILE}")
    print(f"Plaintext credentials for distribution written to {CREDENTIALS_CSV}")
    print("Delete or secure that CSV once credentials have been handed out.")


if __name__ == "__main__":
    main()
