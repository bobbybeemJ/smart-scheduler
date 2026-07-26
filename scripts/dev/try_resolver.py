"""Phase 1 manual verification: exercises all 6 required parsing categories from the
assignment brief against a fixed `now`, printing the resolved constraints for a human eyeball
check. The codified pytest versions of these same scenarios come later in Phase 9."""

import datetime as dt

from app.dateresolve.resolver import resolve, resolve_contextual_duration
from app.schemas import (
    CalendarArithmetic,
    CalendarArithmeticExpr,
    ContextualReference,
    DeadlineBefore,
    DynamicBuffer,
    EventRelative,
    RelativeRangeWithExclusions,
    SimpleDateTime,
    TimePreference,
)
from tests.fixtures.calendar_fixtures import (
    PROJECT_ALPHA_KICKOFF,
    make_event,
    make_find_event,
    make_find_last_meeting,
)

# Wednesday, chosen so "next Friday", "next week", and weekday exclusions all have unambiguous
# expected answers to eyeball.
NOW = dt.datetime(2026, 7, 22, 9, 0)


def show(label: str, constraints) -> None:
    print(f"\n--- {label} ---")
    for window in constraints.search_windows:
        print(f"  search window: {window.start} -> {window.end}")
    print(f"  duration_minutes: {constraints.duration_minutes}")
    if constraints.hard_deadline:
        print(f"  hard_deadline: {constraints.hard_deadline}")
    if constraints.earliest_start:
        print(f"  earliest_start: {constraints.earliest_start}")
    if constraints.excluded_weekdays:
        print(f"  excluded_weekdays (0=Mon): {constraints.excluded_weekdays}")
    if constraints.time_preference:
        print(f"  time_preference: {constraints.time_preference}")


def main() -> None:
    print(f"now = {NOW} ({NOW.strftime('%A')})")

    show(
        "deadline_before: '45 min before my flight Friday at 6 PM'",
        resolve(DeadlineBefore(duration_minutes=45, anchor_weekday="Friday", anchor_time="18:00"), NOW),
    )

    find_event = make_find_event({"Project Alpha Kick-off": PROJECT_ALPHA_KICKOFF})
    show(
        "event_relative: '15-min chat a day or two after Project Alpha Kick-off'",
        resolve(
            EventRelative(duration_minutes=15, event_name="Project Alpha Kick-off", offset_days_min=1, offset_days_max=2),
            NOW,
            find_event=find_event,
        ),
    )

    show(
        "calendar_arithmetic: '1-hour meeting for the last weekday of this month'",
        resolve(CalendarArithmetic(duration_minutes=60, expression=CalendarArithmeticExpr.LAST_WEEKDAY_OF_MONTH), NOW),
    )

    show(
        "relative_range_with_exclusions: 'next week, not too early, not on Wednesday'",
        resolve(
            RelativeRangeWithExclusions(
                duration_minutes=30,
                week_offset=1,
                exclude_weekdays=["Wednesday"],
                time_preference=TimePreference.NOT_TOO_EARLY,
            ),
            NOW,
        ),
    )

    last_meeting_today = make_event(
        "1:1 with manager",
        dt.datetime.combine(NOW.date(), dt.time(17, 0)),
        dt.datetime.combine(NOW.date(), dt.time(17, 30)),
    )
    find_last_meeting = make_find_last_meeting({NOW.date(): last_meeting_today})
    show(
        "dynamic_buffer: 'evening, after 7, need an hour to decompress after my last meeting'",
        resolve(
            DynamicBuffer(duration_minutes=30, after_time="19:00", buffer_minutes=60),
            NOW,
            find_last_meeting=find_last_meeting,
        ),
    )

    duration = resolve_contextual_duration(
        ContextualReference(reference="our usual sync-up"), known_defaults={"usual sync-up": 30}
    )
    print("\n--- contextual_reference: 'our usual sync-up' ---")
    print(f"  resolved duration_minutes: {duration}")

    show(
        "simple_datetime: 'Tuesday at 2pm' (exact time stated)",
        resolve(SimpleDateTime(duration_minutes=30, raw_phrase="Tuesday at 2pm"), NOW),
    )
    show(
        "simple_datetime: 'tomorrow morning' (day-part, no exact time)",
        resolve(SimpleDateTime(duration_minutes=30, raw_phrase="tomorrow morning"), NOW),
    )
    show(
        "simple_datetime: 'next Monday' (date only, no time)",
        resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next Monday"), NOW),
    )


if __name__ == "__main__":
    main()
