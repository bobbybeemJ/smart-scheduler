"""Assignment scenario 3 (calendar-arithmetic): "1-hour meeting for the last weekday of this
month" - pure date arithmetic, no calendar lookup needed. Checked against several different
`now` values so the answer doesn't depend on which day of the month "now" happens to be,
including the tricky case where the calendar month's last day itself falls on a weekend."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import CalendarArithmetic, CalendarArithmeticExpr  # noqa: E402


def test_calendar_arithmetic_extracted_correctly_from_mock_llm():
    intent = extract_intent("1-hour meeting for the last weekday of this month")
    assert isinstance(intent, CalendarArithmetic)
    assert intent.duration_minutes == 60
    assert intent.expression == CalendarArithmeticExpr.LAST_WEEKDAY_OF_MONTH


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
