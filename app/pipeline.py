"""The build pipeline: build_schedule plus every post-pass, in order.

Lives here (not inline in streamlit_app._run_build_schedule) so the app and the
offline test harnesses run EXACTLY the same sequence — the order matters and
each step exists because of a specific real-data finding (see the comments).
Session-state plumbing (special TL-trainee instructions, locked segments,
display tables) stays in the app; this is pure DataFrame in / DataFrame out.
"""

from app import scheduler as S
from app.polish import (polish_schedule, repair_shift_end_edges, convert_attendant_trainees,
                        fill_critical_by_release, unify_evening_rounds)


def is_empty_schedule(schedule_df):
    """True when build_schedule produced no task rows at all. It returns a bare
    pd.DataFrame([]) — no columns — when every flight was skipped, e.g. all of
    them look like FERRY flights because the FIDS (the only source of Pax)
    didn't match the daily roster (real 11.09.2026 case). Every post-pass
    indexes columns like "תפקיד בסיס" and would crash on it."""
    return schedule_df is None or schedule_df.empty or "תפקיד בסיס" not in schedule_df.columns


def run_build_pipeline(flights_df, sched_emps, pre_assignments=None, late_shift_from=None):
    """late_shift_from: minutes — set for the NIGHT segment of a segmented build so
    the polish prefers people already on shift over pulling in the day shift."""
    schedule_df = S.build_schedule(flights_df, sched_emps, pre_assignments=pre_assignments)
    if is_empty_schedule(schedule_df):
        return schedule_df
    # Reserve dual-certified (ר"צ+TSA) workers for uncovered inspector slots
    # BEFORE upgrade_teamleads, so any ר"צ slot it vacates can be back-filled.
    schedule_df = S.reserve_dual_certified_for_tsa(schedule_df, sched_emps)
    schedule_df = S.upgrade_teamleads(schedule_df, sched_emps)
    schedule_df = S.optimize_tl_continuity(schedule_df, sched_emps)
    schedule_df = S.fix_wasteful_gaps(schedule_df, sched_emps)
    schedule_df = S.pair_trainee_attendants(schedule_df, sched_emps)
    schedule_df = S.consolidate_tsa_inspectors_by_pier(schedule_df, sched_emps)
    schedule_df = S.backfill_remaining_gaps(schedule_df, sched_emps)
    schedule_df = S.protect_early_shift_preflight_breaks(schedule_df, sched_emps)
    # LAST: re-assert the trainee/mentor pairing invariant — the passes
    # above can move either side of a pair independently.
    schedule_df = S.improve_night_continuity(schedule_df, sched_emps)
    schedule_df = S.avoid_fresh_start_assignments(schedule_df, sched_emps)
    schedule_df = S.fill_idle_gaps(schedule_df, sched_emps)
    schedule_df = S.enforce_trainee_pairing(schedule_df, sched_emps)
    # ר"צים get priority over plain attendants for floor work, then idle time
    # between a worker's flights is traded/handed away (user rules 2026-09-20).
    # After enforce_trainee_pairing (both passes skip trainee flights) and
    # before the final fix_wasteful_gaps cleanup.
    schedule_df = S.boost_runner_floor_time(schedule_df, sched_emps)
    # ...then level the floor work among the ר"צים themselves (one holds five
    # back-to-back flights while another idles for hours — user 2026-09-22).
    schedule_df = S.boost_runner_floor_time(schedule_df, sched_emps, balance=True)
    # During a ר"צ course, finish certifying the new TLs as fast as possible:
    # give mentor/certifier ר"צים priority for the ראש צוות slot over other
    # ר"צים whenever it unlocks a שיבוץ ready for a free טרייני ר"צ (user rule
    # 2026-09-22). Runs after every pass above that can still move a ר"צ.
    schedule_df = S.maximize_tl_trainee_coverage(schedule_df, sched_emps, flights_df)
    # A ר"צ trainee already on a mentor-led flight as a plain דייל becomes its
    # trainee slot (the vacated דייל slot is back-filled) — user 2026-09-25.
    schedule_df = convert_attendant_trainees(schedule_df, sched_emps, flights_df)
    schedule_df = S.compact_idle_gaps(schedule_df, sched_emps)
    # Re-run wasteful-gap cleanup AFTER every pass that can still reassign a
    # flight (pair_trainee_attendants / backfill_remaining_gaps /
    # enforce_trainee_pairing all hand vacated slots to a substitute without
    # checking whether doing so strands THEM with a purposeless gap). The
    # first fix_wasteful_gaps call above only sees the schedule as it stood
    # right after optimize_tl_continuity — found via real 12.07.2026 data:
    # agent#67 (03:30-11:00) had a clean day until enforce_trainee_pairing
    # backfilled LY323 (08:25-09:30) onto her at the very end, isolated by a
    # ~2h25m gap after her break with nothing to bridge it — she'd have gone
    # back to the counters and back down again for one flight ("טרטור").
    schedule_df = S.fix_wasteful_gaps(schedule_df, sched_emps)
    # Whole-schedule polish (user review of the real 23.08 night build,
    # 2026-09-25): one cost function — dead time, ר"צ-flight imbalance within a
    # shift group, workers brought down for a single flight, a break that only
    # lands at the end of the shift, tasks at the shift edges — and a local
    # search over single-slot hand-overs / two-slot swaps that respect every
    # hard rule. Runs AFTER the final fix_wasteful_gaps: that pass hands tasks to
    # free workers and re-created ~30 single-flight workers the polish had just
    # removed (measured on the 23.08 build: cost 977 -> 2094).
    schedule_df = polish_schedule(schedule_df, sched_emps, late_shift_from=late_shift_from)
    schedule_df = repair_shift_end_edges(schedule_df, sched_emps)
    # A ❌ inspector / ר"צ slot the polish left next to a certified worker it parked
    # on a lesser role (23.08: TSA-inspector#3 on a דייל slot, LY017's inspector ❌) —
    # free that worker and hand the lesser task to someone else (user 2026-09-27).
    schedule_df = fill_critical_by_release(schedule_df, sched_emps)
    # An evening worker (14:00-19:30 start) who went down in the afternoon AND for the
    # night block goes down once: hand the movable side to someone with nothing
    # (user 2026-08-06 rule, extended to 14:00 starts on 2026-09-27).
    schedule_df = unify_evening_rounds(schedule_df, sched_emps)
    # Last: no first task in the first 45 min of a shift, on any terminal (user 2026-09-25).
    schedule_df = S.avoid_fresh_start_assignments(schedule_df, sched_emps, terminal=None)
    return schedule_df
