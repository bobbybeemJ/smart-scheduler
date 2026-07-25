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
    NOT_TOO_EARLY = "not_too_early"
    NOT_TOO_LATE = "not_too_late"


class CalendarArithmeticExpr(str, Enum):
    LAST_WEEKDAY_OF_MONTH = "last_weekday_of_month"


class DeadlineBefore(BaseModel):
    """"45 minutes sometime before my flight Friday at 6 PM" """

    kind: Literal["deadline_before"] = "deadline_before"
    duration_minutes: Optional[int] = None
    anchor_weekday: str
    anchor_time: str
    buffer_minutes: int = 0


class EventRelative(BaseModel):
    """"a 15-minute chat a day or two after the Project Alpha Kick-off event" """

    kind: Literal["event_relative"] = "event_relative"
    duration_minutes: Optional[int] = None
    event_name: str
    offset_days_min: int
    offset_days_max: int


class CalendarArithmetic(BaseModel):
    """"1-hour meeting for the last weekday of this month" """

    kind: Literal["calendar_arithmetic"] = "calendar_arithmetic"
    duration_minutes: Optional[int] = None
    expression: CalendarArithmeticExpr


class RelativeRangeWithExclusions(BaseModel):
    """"next week, not too early, not on Wednesday" """

    kind: Literal["relative_range_with_exclusions"] = "relative_range_with_exclusions"
    duration_minutes: Optional[int] = None
    range: Literal["next_week", "this_week"] = "next_week"
    exclude_weekdays: list[str] = Field(default_factory=list)
    time_preference: Optional[TimePreference] = None


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


TemporalExpression = Union[
    DeadlineBefore,
    EventRelative,
    CalendarArithmetic,
    RelativeRangeWithExclusions,
    ContextualReference,
    DynamicBuffer,
    SimpleDateTime,
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
    excluded_weekdays: list[int] = Field(default_factory=list)  # 0=Monday .. 6=Sunday
    time_preference: Optional[TimePreference] = None
