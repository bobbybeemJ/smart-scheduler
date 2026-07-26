"""Assignment scenario 3 (calendar-arithmetic): "1-hour meeting for the last weekday of this
month" - pure date arithmetic, no calendar lookup needed. Also covers the generalized form this
grew into: "the Nth <day-type> of <month>" for any ordinal/day-type/month combination, not just
last/first-weekday-of-this-month - see CalendarArithmetic's docstring for why that generalization
replaced what used to be one hardcoded enum value per phrase."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import CalendarArithmetic  # noqa: E402


def test_calendar_arithmetic_extracted_correctly_from_mock_llm():
    intent = extract_intent("1-hour meeting for the last weekday of this month")
    assert isinstance(intent, CalendarArithmetic)
    assert intent.duration_minutes == 60
    assert intent.ordinal == "last"
    assert intent.day_type == "weekday"


def test_last_weekday_of_month_when_the_last_calendar_day_is_already_a_weekday():
    # July 2026 has 31 days; July 31 2026 is a Friday.
    now = dt.datetime(2026, 7, 22, 9, 0)
    intent = extract_intent("1-hour meeting for the last weekday of this month")
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 31)
    assert window.start.date().weekday() == 4  # Friday


def test_last_weekday_of_month_walks_back_from_a_weekend_month_end():
    # August 2026 has 31 days; August 31 2026 is a Monday, so August doesn't hit this case -
    # use a month whose last calendar day IS a weekend: May 2026 ends on Sunday, May 31.
    now = dt.datetime(2026, 5, 1, 9, 0)
    intent = extract_intent("1-hour meeting for the last weekday of this month")
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 5, 29)  # Friday, walked back from Sunday the 31st
    assert window.start.date().weekday() == 4


def test_last_weekday_of_month_is_independent_of_what_day_now_is_within_the_month():
    """The answer must be the same regardless of whether "now" is the 1st or the 25th."""
    intent = extract_intent("1-hour meeting for the last weekday of this month")
    early_in_month = resolve(intent, dt.datetime(2026, 7, 2, 9, 0))
    late_in_month = resolve(intent, dt.datetime(2026, 7, 29, 9, 0))

    assert early_in_month.search_windows[0].start.date() == late_in_month.search_windows[0].start.date()


def test_first_weekday_of_month_extracted_correctly_from_mock_llm():
    """Found necessary via testing real Gemini: with only "last weekday" supported, asking for
    the FIRST weekday of a month didn't fail - it silently forced the only available answer and
    returned last_weekday_of_month instead, a confidently wrong answer with no error."""
    intent = extract_intent("1-hour meeting for the first weekday of this month")
    assert isinstance(intent, CalendarArithmetic)
    assert intent.ordinal == "first"
    assert intent.day_type == "weekday"


def test_first_weekday_of_month_when_the_first_calendar_day_is_already_a_weekday():
    # July 1 2026 is a Wednesday.
    now = dt.datetime(2026, 7, 1, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="first", day_type="weekday")
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 1)
    assert window.start.date().weekday() == 2  # Wednesday


def test_first_weekday_of_month_walks_forward_from_a_weekend_month_start():
    # August 2026 starts on Saturday, August 1 - must walk forward to Monday, August 3.
    now = dt.datetime(2026, 7, 22, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="first", day_type="weekday", month_offset=1)
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 8, 3)
    assert window.start.date().weekday() == 0  # Monday


def test_last_weekday_of_month_with_month_offset_targets_a_different_month():
    """month_offset (mirroring RelativeRangeWithExclusions.week_offset) generalizes this beyond
    just "this month" - "the last weekday of next month" needs its own signal distinct from
    ordinal/day_type, or it hits the exact same silent-wrong-answer failure mode all over again."""
    now = dt.datetime(2026, 4, 15, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="last", day_type="weekday", month_offset=1)
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 5, 29)  # Friday, walked back from Sunday May 31
    assert window.start.date().weekday() == 4


def test_second_tuesday_of_next_month_extracted_correctly_from_mock_llm():
    """The real generalization target: an arbitrary ordinal x specific-weekday combination, not
    just first/last-of-any-business-day. Previously impossible to express at all - real Gemini
    either misclassified this as simple_datetime (mangled by dateparser) or, after out_of_scope
    was added, correctly refused to guess rather than mis-resolve it. Neither is as good as
    actually supporting the request."""
    intent = extract_intent("book a 1 hour meeting for the second tuesday of next month")
    assert isinstance(intent, CalendarArithmetic)
    assert intent.ordinal == "second"
    assert intent.day_type == "tuesday"
    assert intent.month_offset == 1


def test_second_tuesday_of_next_month_resolves_to_the_correct_date():
    # August 2026: Tuesdays fall on 4, 11, 18, 25 - the second is August 11.
    now = dt.datetime(2026, 7, 22, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="second", day_type="tuesday", month_offset=1)
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 8, 11)
    assert window.start.date().weekday() == 1  # Tuesday


def test_last_friday_of_month_extracted_correctly_from_mock_llm():
    """Distinct from "last weekday of the month" (any Mon-Fri business day) - this names a
    specific day-of-week, which can land on an earlier date than the last business day."""
    intent = extract_intent("a 1 hour meeting for the last friday of this month")
    assert isinstance(intent, CalendarArithmetic)
    assert intent.ordinal == "last"
    assert intent.day_type == "friday"


def test_last_friday_of_month_differs_from_last_weekday_when_month_ends_midweek():
    # July 2026 ends on Friday the 31st, so last-Friday and last-weekday coincide here - use a
    # month that does NOT end on a Friday to actually distinguish them: August 2026 ends on
    # Monday the 31st, so "last weekday" is Aug 31 but "last Friday" is Aug 28.
    now = dt.datetime(2026, 8, 1, 9, 0)
    last_friday = resolve(CalendarArithmetic(duration_minutes=60, ordinal="last", day_type="friday"), now)
    last_weekday = resolve(CalendarArithmetic(duration_minutes=60, ordinal="last", day_type="weekday"), now)

    assert last_friday.search_windows[0].start.date() == dt.date(2026, 8, 28)
    assert last_weekday.search_windows[0].start.date() == dt.date(2026, 8, 31)
    assert last_friday.search_windows[0].start.date() != last_weekday.search_windows[0].start.date()


def test_fifth_occurrence_of_a_weekday_that_the_month_does_not_have_raises_not_silently_wrong():
    """FIFTH is a real, reachable ordinal (some months do have a 5th occurrence of a given
    weekday) - but when the target month only has four, this must fail loudly rather than
    silently returning the fourth (or some other wrong date) as if it were the fifth."""
    # July 2026: Mondays fall on 6, 13, 20, 27 - only four, no fifth.
    now = dt.datetime(2026, 7, 1, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="fifth", day_type="monday")
    try:
        resolve(intent, now)
        assert False, "expected a ValueError - July 2026 has no fifth Monday"
    except ValueError as exc:
        assert "fifth" in str(exc) and "monday" in str(exc)


def test_fifth_occurrence_of_a_weekday_the_month_does_have_resolves_correctly():
    # July 2026: Wednesdays fall on 1, 8, 15, 22, 29 - five of them, since the month starts on
    # one of its first three weekdays (Wed/Thu/Fri all get five occurrences in a 31-day month).
    now = dt.datetime(2026, 7, 1, 9, 0)
    intent = CalendarArithmetic(duration_minutes=60, ordinal="fifth", day_type="wednesday")
    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 29)
    assert window.start.date().weekday() == 2  # Wednesday
