"""Manual verification of slot_finder.py + ranking.py against fixture freebusy data (no real
Calendar API calls needed for this - deterministic and fast). Covers:
1. Multi-day window business-hours clamping.
2. time_preference applied uniformly across every day, not just first/last.
3. Ranking demonstrably not "first chronological slot."
4. No-slots -> widened-search fallback.
"""

import datetime as dt

from app.dateresolve.resolver import resolve
from app.schemas import DeadlineBefore, RelativeRangeWithExclusions, TimePreference
from app.scheduling.ranking import rank_candidates
from app.scheduling.slot_finder import find_available_slots, find_available_slots_with_fallback

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def no_busy(start, end):
    return []


def fully_busy(start, end):
    return [{"start": start, "end": end}]


def show(label, slots):
    print(f"\n--- {label} ---")
    for s in slots:
        print(f"  {s.start} -> {s.end}")


def main():
    # 1. Multi-day deadline_before window: today (Wed) -> Friday 6pm deadline.
    constraints = resolve(DeadlineBefore(duration_minutes=45, anchor_weekday="Friday", anchor_time="18:00"), NOW)
    slots = find_available_slots(constraints, freebusy_fn=no_busy, max_results=100)
    show("deadline_before, all free (checking bounds only)", slots[:3] + slots[-3:])
    bad = [s for s in slots if s.start.hour < 9 or s.end.hour > 18 or (s.end.hour == 18 and s.end.minute > 0)]
    assert not bad, f"found out-of-bounds slots: {bad}"
    last_slot = max(slots, key=lambda s: s.end)
    deadline = dt.datetime(2026, 7, 24, 18, 0)
    assert last_slot.end <= deadline, f"last slot {last_slot.end} must not exceed the 6pm deadline"
    assert last_slot.end >= deadline - dt.timedelta(minutes=30), (
        f"last slot {last_slot.end} stopped too early - should search right up to the deadline, not a generic 5pm cutoff"
    )
    print(f"OK: no slot before 9am on middle days, last slot ({last_slot.end}) searches right up to the 6pm deadline, not a generic 5pm cutoff")

    # 2. next_week, not_too_early: floor=10, ceiling=18 should apply to EVERY day, not just Mon/Fri.
    constraints2 = resolve(
        RelativeRangeWithExclusions(duration_minutes=30, week_offset=1, exclude_weekdays=["Wednesday"], time_preference=TimePreference.NOT_TOO_EARLY),
        NOW,
    )
    slots2 = find_available_slots(constraints2, freebusy_fn=no_busy, max_results=100)
    by_day = {}
    for s in slots2:
        by_day.setdefault(s.start.date(), []).append(s)
    for day, day_slots in sorted(by_day.items()):
        earliest = min(s.start for s in day_slots)
        latest = max(s.end for s in day_slots)
        assert earliest.hour >= 10, f"{day}: earliest slot {earliest} violates the 10am floor"
        assert latest.hour <= 18, f"{day}: latest slot end {latest} violates the 18:00 ceiling"
        assert day.weekday() != 2, f"Wednesday {day} should have been excluded entirely"
    print(f"OK: 10am floor / 6pm ceiling applied on all {len(by_day)} days (Mon/Tue/Thu/Fri), Wednesday fully excluded")

    # 3. Ranking: deliberately construct candidates out of preference order and confirm ranking fixes it.
    early = slots2[0]  # chronologically first (Monday ~10am)
    late = [s for s in slots2 if s.start.hour >= 16][0]  # a late-afternoon slot, later chronologically
    unordered = [early, late]
    ranked = rank_candidates(unordered, constraints2)
    assert ranked[0] == late, "not_too_early should rank the later slot first, not just return chronological order"
    print(f"OK: ranking picked {ranked[0].start} (later) over {early.start} (chronologically first) due to not_too_early")

    # 4. No-slots -> widened fallback.
    candidates, was_widened = find_available_slots_with_fallback(constraints2, freebusy_fn=fully_busy)
    assert was_widened is False and candidates == [], "fully busy should yield nothing even after widening in this fixture"
    print("OK: fully-busy calendar correctly yields no candidates even after widening (fixture has no free time anywhere)")

    def busy_only_original_window(start, end):
        # Busy for anything before Aug 1, free after - simulates "original week is booked solid,
        # but the widened search finds something."
        if end <= dt.datetime(2026, 8, 1):
            return [{"start": start, "end": end}]
        return []

    candidates2, was_widened2 = find_available_slots_with_fallback(constraints2, freebusy_fn=busy_only_original_window)
    assert was_widened2 is True and len(candidates2) > 0
    print(f"OK: original window fully busy -> widened search found {len(candidates2)} alternative slot(s), e.g. {candidates2[0].start}")


if __name__ == "__main__":
    main()
