"""Structured intent shapes. The LLM (Phase 2) only ever fills in these fields from natural
language - it never computes an actual date/time itself. Every field here is a raw extracted
value (a weekday name, an hour string, an event name, a day-offset) that Python resolves
deterministically in app/dateresolve/resolver.py."""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class TimePreference(str, Enum):
    """Hour-of-day preference within a given day (e.g. "not too early in the morning") -
    distinct from WeekPosition, which is about which days of the range to favor."""

    NOT_TOO_EARLY = "not_too_early"
    NOT_TOO_LATE = "not_too_late"


class WeekPosition(str, Enum):
    """Which part of the date range to favor (e.g. "late next week" means toward Thu/Fri, not
    "not too late in the day"). Found via testing the assignment's own example phrase - without
    this, "late next week" was being conflated with TimePreference.NOT_TOO_LATE, which actually
    means "prefer earlier hours," the opposite dimension entirely."""

    EARLY_IN_RANGE = "early_in_range"
    LATE_IN_RANGE = "late_in_range"


class CalendarArithmeticExpr(str, Enum):
    """Found via testing real Gemini: with only LAST_WEEKDAY_OF_MONTH available, asking for "the
    FIRST weekday of next month" didn't fail or fall back gracefully - the model just forced the
    only enum value it had, silently returning last_weekday_of_month for a request that
    explicitly said "first." That's worse than a dropped field: confidently wrong output with no
    error at all. FIRST_WEEKDAY_OF_MONTH closes that specific hole. (Arbitrary "Nth weekday of
    month," e.g. "the second Tuesday," is a deliberately separate, unimplemented case - the
    assignment brief's own example is only ever "last weekday of the month," and Nth-weekday-of-
    month is a materially bigger feature; see SimpleDateTime's dateparser fallback, which handles
    it approximately but not reliably.)"""

    LAST_WEEKDAY_OF_MONTH = "last_weekday_of_month"
    FIRST_WEEKDAY_OF_MONTH = "first_weekday_of_month"


class DeadlineBefore(BaseModel):
    """"45 minutes sometime before my flight Friday at 6 PM" """

    kind: Literal["deadline_before"] = "deadline_before"
    duration_minutes: Optional[int] = None
    anchor_weekday: str
    anchor_time: str
    buffer_minutes: int = 0
    earliest_time: Optional[str] = None
    """"nothing before 9am" - an HH:MM (24h) floor applying to every day the search window
    touches, not just the anchor day. Found missing via testing real Gemini: this constraint
    was silently dropped with no error at all before this field existed, identical to the
    event_relative gap below. Only set this when the user states or clearly implies a literal
    hour - never invent one for a vague "not too early" (that's a different, unrelated concept:
    this project doesn't currently support a vague preference on this schema kind)."""


class EventRelative(BaseModel):
    """"a 15-minute chat a day or two after the Project Alpha Kick-off event" """

    kind: Literal["event_relative"] = "event_relative"
    duration_minutes: Optional[int] = None
    event_name: str
    offset_days_min: int
    offset_days_max: int
    earliest_time: Optional[str] = None
    """"not before 11am" - an HH:MM (24h) floor applying to every day in the offset window.
    Found missing via testing real Gemini on exactly this phrase: the constraint was silently
    dropped with zero error or trace of it in the extracted intent. Only set this when the user
    states or clearly implies a literal hour."""


class CalendarArithmetic(BaseModel):
    """"1-hour meeting for the last weekday of this month" """

    kind: Literal["calendar_arithmetic"] = "calendar_arithmetic"
    duration_minutes: Optional[int] = None
    expression: CalendarArithmeticExpr
    month_offset: int = 0
    """Signed integer counting calendar months from the CURRENT month, mirroring
    RelativeRangeWithExclusions.week_offset. 0 = this month, 1 = next month, etc. Added
    alongside FIRST_WEEKDAY_OF_MONTH so "the last weekday of next month" doesn't hit the exact
    same silent-wrong-enum failure mode all over again for the month dimension."""


class RelativeRangeWithExclusions(BaseModel):
    """"next week, not too early, not on Wednesday" - also covers "sometime late next week"
    via week_position (which days of the range) as distinct from time_preference (which hours
    within a day).

    week_offset is a signed integer relative to the CURRENT calendar week: 0 = this week,
    1 = next week, 2 = the week after next / "two weeks from now", -1 = last week, etc. This
    replaced a two-value range: Literal["this_week", "next_week"] enum after real testing showed
    Gemini would simply abandon this schema entirely for anything slightly different ("the week
    after next", "two weeks from now") and fall back to SimpleDateTime instead - silently
    dropping exclude_weekdays/time_preference/week_position in the process, since SimpleDateTime
    has none of those fields. A signed integer lets the LLM express any week offset uniformly.
    Negative values are still accepted here (extraction should reflect what the user actually
    said) - resolver.py's resolve() rejects an entirely-past result for ANY case, not just this
    one, so "last week" is caught centrally rather than needing per-case validation."""

    kind: Literal["relative_range_with_exclusions"] = "relative_range_with_exclusions"
    duration_minutes: Optional[int] = None
    week_offset: int = 1
    exclude_weekdays: list[str] = Field(default_factory=list)
    time_preference: Optional[TimePreference] = None
    week_position: Optional[WeekPosition] = None


class ContextualReference(BaseModel):
    """"our usual sync-up" - has no standalone window; the dialogue layer (Phase 3) resolves
    the duration from session state and merges it with whatever other constraint is active."""

    kind: Literal["contextual_reference"] = "contextual_reference"
    reference: str


class DynamicBuffer(BaseModel):
    """"evening, after 7, but I need an hour to decompress after my last meeting" """

    kind: Literal["dynamic_buffer"] = "dynamic_buffer"
    duration_minutes: Optional[int] = None
    after_time: str
    buffer_minutes: int
    buffer_source: Literal["last_meeting_today"] = "last_meeting_today"


class SimpleDateTime(BaseModel):
    """Not one of the 6 hard cases from the assignment brief, but the most common thing a real
    user says: "Tuesday at 2pm", "tomorrow morning", "next Monday". `raw_phrase` is resolved by
    dateparser (deterministic, not the LLM) in the resolver."""

    kind: Literal["simple_datetime"] = "simple_datetime"
    duration_minutes: Optional[int] = None
    raw_phrase: str


class DurationUpdate(BaseModel):
    """"actually we need a full hour now" - a correction to an already-established duration,
    with day/time constraints unchanged. Only used when the message is purely a duration
    correction to a meeting already being discussed; the dialogue layer (Phase 3) merges this
    into the existing established expression and re-resolves, rather than restarting the
    conversation."""

    kind: Literal["duration_update"] = "duration_update"
    duration_minutes: int


class SlotDecision(BaseModel):
    """The user is responding to 1-3 candidate slots that were JUST offered (condensed session
    state shows phase == "confirming" and num_offered_candidates > 0) - only extract this kind
    in that situation, never otherwise. Backs up dialogue/manager.py's fast local string-
    matching, which handles the common unambiguous cases ("yes", "the second one") at zero LLM
    cost without ever reaching you; you're only asked when that matching was ambiguous. Found
    necessary via testing real Gemini: without this kind, natural phrasings like "yup, sounds
    good, let's do that" or "let's go with the earlier one" were being misclassified as
    contextual_reference, derailing the conversation instead of booking or clarifying."""

    kind: Literal["slot_decision"] = "slot_decision"
    decision: Literal["confirm_top", "select_index", "reject_all"]
    selected_index: Optional[int] = None
    """0-based index into the offered candidates - only meaningful (and only ever set) when
    decision == "select_index"."""


class OutOfScope(BaseModel):
    """The message isn't a scheduling request at all - cancellations, unrelated questions, small
    talk, anything that isn't "find/book a new meeting slot." Found necessary via testing real
    Gemini: without an explicit "none of the above" escape hatch, response_schema forces the
    model to pick some other kind no matter what it's given, so "cancel my 3pm meeting tomorrow"
    and "what's the weather today" were both silently coerced into fake simple_datetime booking
    attempts instead of being recognized as outside this assistant's job."""

    kind: Literal["out_of_scope"] = "out_of_scope"


TemporalExpression = Union[
    DeadlineBefore,
    EventRelative,
    CalendarArithmetic,
    RelativeRangeWithExclusions,
    ContextualReference,
    DynamicBuffer,
    SimpleDateTime,
    DurationUpdate,
    SlotDecision,
    OutOfScope,
]


class TimeWindow(BaseModel):
    start: dt.datetime
    end: dt.datetime


class ResolvedConstraints(BaseModel):
    """Deterministic output of resolver.resolve() - everything Phase 7's slot_finder needs to
    search real freebusy data for a candidate slot. No hallucinated times, only computed ones."""

    duration_minutes: int
    search_windows: list[TimeWindow]
    hard_deadline: Optional[dt.datetime] = None
    earliest_start: Optional[dt.datetime] = None
    earliest_hour: Optional[int] = None
    """A literal hour-of-day floor (0-23) applying to EVERY day the search window touches -
    distinct from earliest_start, which anchors to one specific date (dynamic_buffer's "today
    only, after my last meeting"). Powers EventRelative/DeadlineBefore's earliest_time field."""
    excluded_weekdays: list[int] = Field(default_factory=list)  # 0=Monday .. 6=Sunday
    time_preference: Optional[TimePreference] = None
