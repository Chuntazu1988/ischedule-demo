"""Look-ahead for segmented (נייט / דיי / אפטר) builds.

A segment is built by one shift team without seeing the demand of the segments
after it, so it can spend a scarce worker on a lesser job right where a later
segment needs them — real 23.08: the דיי build put TSA-inspector#3, the day's only
in-shift מפקח TSA, on a דייל slot (21:15-22:20) next to LY017's inspector slot
(22:15), and the אפטר build, which may not touch published rows, was left with
an empty inspector slot (user 2026-09-27).

The look-ahead is a SHADOW build: the current segment's flights plus every
flight of the segments after it, run through the normal pipeline on the data as
it is RIGHT NOW (so it is recomputed on every build — sick workers, people sent
home and roster edits are all reflected; nothing built earlier tonight is
trusted). From it we take:

  * reservations — the scarce-role slots (מפקח TSA / ראש צוות) the shadow gave
    to workers on LATER flights. The real build seeds them as busy time, so the
    current segment cannot park those workers on an overlapping lesser task.
    They are never published: the caller drops them from the result.
  * a forecast — the slots of later flights the shadow could not fill, so the
    shift manager sees a coming shortage while there is still time to act.

Advisory by design: any failure returns "nothing" and the build proceeds as it
always did."""

import pandas as pd

from app import scheduler as S
from app.pipeline import is_empty_schedule
from utils.helpers import clean_text

SCARCE_ROLES = ("מפקח TSA", "ראש צוות")
LOCKED_COL = "_נעול"
RESERVED_COL = "_שמור"


def _fnums(df):
    return {clean_text(str(v)) for v in df["טיסה"]}


def _light_pipeline(flights_df, sched_emps, locked_df):
    """Just the passes that decide WHO holds the scarce slots and whether a slot
    can be filled at all: the main assignment, the inspector / ר"צ reservations
    and upgrades, pier consolidation and the gap back-fill. The comfort passes
    (breaks, continuity, floor-time, polish...) reshuffle attendants and cannot
    create or remove a מפקח / ראש צוות shortage, and they were most of the
    shadow's run time."""
    df = S.build_schedule(flights_df, sched_emps, pre_assignments=locked_df)
    if is_empty_schedule(df):
        return df
    for fn in (S.reserve_dual_certified_for_tsa, S.upgrade_teamleads,
               S.consolidate_tsa_inspectors_by_pier, S.backfill_remaining_gaps):
        df = fn(df, sched_emps)
    return df


def shadow_lookahead(build_flights, later_flights, sched_emps, locked_df, late_shift_from=None):
    """Returns (reservations_df | None, forecast list, shadow's missing count on the
    CURRENT flights). `build_flights` / `later_flights` are already the schedule's
    effective (ETD) frames; `locked_df` is what earlier segments published."""
    if later_flights is None or later_flights.empty:
        return None, [], 0
    everything = pd.concat([build_flights, later_flights], ignore_index=True)
    shadow = _light_pipeline(everything, sched_emps, locked_df)
    if is_empty_schedule(shadow):
        return None, [], 0
    flight = shadow["טיסה"].astype(str).map(clean_text)
    later_mask = flight.isin(_fnums(later_flights))
    current_mask = flight.isin(_fnums(build_flights)) & ~later_mask
    if LOCKED_COL in shadow.columns:
        fresh = shadow[LOCKED_COL].map(lambda v: v is not True)
    else:
        fresh = pd.Series(True, index=shadow.index)
    missing = shadow["עובד"].astype(str).str.contains("❌", na=False)
    role = shadow["תפקיד בסיס"].astype(str).map(clean_text)

    forecast = [
        {"flight": clean_text(str(r["טיסה"])), "time": str(r["התחלה"]), "role": clean_text(str(r["תפקיד בסיס"]))}
        for r in shadow[later_mask & missing & fresh].to_dict("records")
    ]
    reserve = shadow[later_mask & ~missing & fresh & role.isin(SCARCE_ROLES)].copy()
    if reserve.empty:
        reservations = None
    else:
        reserve[LOCKED_COL] = True
        reserve[RESERVED_COL] = True
        reservations = reserve
    return reservations, forecast, int((current_mask & missing & fresh).sum())


def count_missing(schedule_df, flights_df):
    """❌ rows of `flights_df`'s flights in `schedule_df` (reserved rows excluded)."""
    if schedule_df is None or schedule_df.empty:
        return 0
    nums = _fnums(flights_df)
    flight = schedule_df["טיסה"].astype(str).map(clean_text)
    miss = schedule_df["עובד"].astype(str).str.contains("❌", na=False)
    return int((flight.isin(nums) & miss).sum())


def drop_reserved(schedule_df):
    """Remove the reservation rows before the schedule is stored or shown."""
    if schedule_df is None or RESERVED_COL not in schedule_df.columns:
        return schedule_df
    keep = schedule_df[RESERVED_COL].map(lambda v: v is not True)
    return schedule_df[keep].drop(columns=[RESERVED_COL]).copy()
