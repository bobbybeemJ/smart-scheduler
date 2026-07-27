"""Structured intent shapes. The LLM only ever fills in these fields from natural language - it
never computes an actual date/time itself. Every field here is a raw extracted value (a weekday
name, an hour string, an event name, a day-offset) that Python resolves deterministically in
app/dateresolve/resolver.py."""

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


class Ordinal(str, Enum):
    """Which occurrence of day_type within the target month. FIFTH is included because some
    months genuinely have a 5th occurrence of a given weekday (e.g. a 31-day month starting on
    that weekday) - leaving it out would silently mis-resolve a rare but real phrase the same way
    LAST_WEEKDAY_OF_MONTH being the only option once did."""

    FIRST = "first"
    SECOND = "second"
    THIRD = "third"
    FOURTH = "fourth"
    FIFTH = "fifth"
    LAST = "last"


DAY_TYPE_WEEKDAY = "weekday"  # any Mon-Fri business day, not a specific day-of-week
DayType = Literal["weekday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class SchedulingRequestBase(BaseModel):
    """Shared by every "kind" that represents an actual new meeting to find/book (everything
    except contextual_reference/duration_update/slot_decision/out_of_scope, which don't carry a
    fresh request of their own). Consolidates duration_minutes, previously duplicated
    identically in all six kinds below - every one of them reads it, unlike earliest_time (see
    HasEarliestTime), which only three of the six actually use.

    A meeting_title field was tried here too (extracting "with Rahul" as a real calendar event
    name instead of a generic default) but reverted - adding it destabilized real Gemini's
    structured output on this free-tier model: the model's own leaked reasoning showed it
    correctly computing the title internally, but the actual meeting_title field still came back
    null while that reasoning text spilled into raw_phrase instead, corrupting date parsing. Not
    worth shipping an unreliable feature - reconsider only if it can be done without adding
    another field to an already-large union."""

    duration_minutes: Optional[int] = None


class HasEarliestTime(BaseModel):
    """Mixed in only by the three kinds whose resolver function actually reads earliest_time
    (deadline_before, event_relative, dynamic_buffer) - deliberately NOT part of
    SchedulingRequestBase itself. That was tried first, and it was a mistake: it put
    earliest_time on all six scheduling kinds, including three (calendar_arithmetic,
    simple_datetime, relative_range_with_exclusions) whose resolver functions never read it at
    all, so a constraint set there would be silently dropped - the exact class of bug this
    project has spent real effort finding and fixing elsewhere. A field only belongs on the base
    every kind shares if every kind's resolver actually honors it.

    earliest_time was previously duplicated on deadline_before/event_relative under this same
    name, AND separately reinvented on dynamic_buffer under the different name "after_time" for
    the exact same concept (an HH:MM clock-time floor) - one name for one concept now, not a
    different field name per kind for something that means the same thing everywhere."""

    earliest_time: Optional[str] = None
    """An HH:MM (24h) clock-time floor - "not before 11am," "nothing before 9am," "after 7pm."
    Only set this when the user states or clearly implies a literal hour - never invent one for
    a vague "not too early" (a different, unrelated concept some kinds express via
    time_preference instead)."""


class DeadlineBefore(SchedulingRequestBase, HasEarliestTime):
    """"45 minutes sometime before my flight Friday at 6 PM" """

    kind: Literal["deadline_before"] = "deadline_before"
    anchor_weekday: str
    anchor_time: Optional[str] = None
    """Found via testing real Gemini on "before I leave for my trip on Friday" (no time stated
    at all): with this field required, the model didn't ask for clarification or leave it
    blank - it invented "18:00" out of nowhere, with zero basis in the input. That's a direct
    violation of this system's core rule (never invent a date/time), identical in spirit to the
    duration_minutes guessing problem rule 2 already guards against - just not applied here.
    Optional + MissingAnchorTimeError (resolver.py) closes the same hole the same way."""
    buffer_minutes: int = 0


class EventRelative(SchedulingRequestBase, HasEarliestTime):
    """"a 15-minute chat a day or two after the Project Alpha Kick-off event" """

    kind: Literal["event_relative"] = "event_relative"
    event_name: str
    offset_days_min: int
    offset_days_max: int


class CalendarArithmetic(SchedulingRequestBase):
    """"1-hour meeting for the last weekday of this month" - and generally, "the Nth <day-type>
    of <month>" for any ordinal/day-type/month combination. Originally this was a single enum of
    whole phrases (just LAST_WEEKDAY_OF_MONTH); adding FIRST_WEEKDAY_OF_MONTH as a second enum
    value for "first weekday of next month" turned out to be the same mistake the old
    this_week/next_week enum was, one level removed - a new phrase always needs a new hardcoded
    value. Decomposing into its real, independent dimensions (which occurrence x which kind of
    day x which month) covers the whole combinatorial space - "second Tuesday", "last Friday",
    "first weekday of next month" - with one resolver function instead of one case per phrase."""

    kind: Literal["calendar_arithmetic"] = "calendar_arithmetic"
    ordinal: Ordinal
    day_type: DayType
    """"weekday" means any Mon-Fri business day (the assignment brief's own "last weekday of the
    month" example). Any other value is a specific day-of-week name - "last Friday of the
    month" is ordinal=LAST, day_type="friday", a materially different date than "last weekday"."""
    month_offset: int = 0
    """Signed integer counting calendar months from the CURRENT month - 0 = this month, 1 = next
    month, etc. Mirrors RelativeRangeWithExclusions.week_offset."""


class RelativeRangeWithExclusions(SchedulingRequestBase):
    """"next week, not too early, not on Wednesday" - also covers "sometime late next week"
    via week_position (which days of the range) as distinct from time_preference (which hours
    within a day). Only for a genuine RANGE of days to search across - if the user names ONE
    specific day (even phrased with "next week", e.g. "next week on Tuesday" - which really just
    means "next Tuesday"), use simple_datetime instead so that day isn't silently lost; this
    schema has no field for "restrict to a single specific weekday," only exclude_weekdays
    (days to AVOID), so a single-day request extracted as this kind loses the day entirely and
    resolves to the whole week instead.

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
    week_offset: int = 1
    exclude_weekdays: list[str] = Field(default_factory=list)
    time_preference: Optional[TimePreference] = None
    week_position: Optional[WeekPosition] = None


class ContextualReference(BaseModel):
    """"our usual sync-up" - has no standalone window; the dialogue layer resolves the duration
    from session state and merges it with whatever other constraint is active."""

    kind: Literal["contextual_reference"] = "contextual_reference"
    reference: str


class DynamicBuffer(SchedulingRequestBase, HasEarliestTime):
    """"evening, after 7, but I need an hour to decompress after my last meeting" -
    earliest_time (a stated clock-time floor, inherited from HasEarliestTime - this kind used to
    call it "after_time" as its own separate field, the same concept reinvented under a
    different name) and buffer_minutes/buffer_source (a floor relative to another event) are
    independent constraints that combine (the later of the two wins), not alternatives - the
    assignment's own example states both at once. Found via testing real Gemini: when a message
    has NO stated clock time at all - just a buffer relative to a named event, e.g. "at least an
    hour after my call with Sarah wraps up" - a required version of this field made the model
    stuff the person's name in here instead, which then crashed trying to parse "Sarah" as a
    time. Leave it null whenever no explicit clock time is stated; buffer_source/
    reference_event_name below carry the "relative to an event" part on their own."""

    kind: Literal["dynamic_buffer"] = "dynamic_buffer"
    buffer_minutes: int
    buffer_source: Literal["last_meeting_today", "named_event"] = "last_meeting_today"
    reference_event_name: Optional[str] = None
    """Only set when buffer_source == "named_event" - e.g. "my call with Sarah" - resolved via
    the same calendar event lookup event_relative already uses. Added alongside making
    earliest_time optional so a named-event reference has an honest field to live in instead of
    being forced into earliest_time."""


class SimpleDateTime(SchedulingRequestBase):
    """Not one of the 6 hard cases from the assignment brief, but the most common thing a real
    user says: "Tuesday at 2pm", "tomorrow morning", "next Monday". `raw_phrase` is resolved by
    dateparser (deterministic, not the LLM) in the resolver."""

    kind: Literal["simple_datetime"] = "simple_datetime"
    raw_phrase: Optional[str] = None
    """Null when the message is a scheduling request that states no day/time at all yet (e.g.
    "I need to schedule a meeting"). Originally a required str, which forced the model to pick
    between inventing something to fill it (violating the "never invent" rule) or misclassifying
    the whole message as out_of_scope - found via live testing: the assignment's own example
    opening line ("I need to schedule a meeting") was being classified as out_of_scope 2/2 times,
    silently telling a legitimate (if incomplete) scheduling request that this assistant "can
    only help schedule a new meeting slot." Optional removes the forced choice - the resolver
    treats null the same as an unparseable phrase (ask for day/time), never a resolver crash."""


class DurationUpdate(BaseModel):
    """"actually we need a full hour now" - a correction to an already-established duration,
    with day/time constraints unchanged. Only used when the message is purely a duration
    correction to a meeting already being discussed; the dialogue layer merges this into the
    existing established expression and re-resolves, rather than restarting the conversation."""

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
    """Deterministic output of resolver.resolve() - everything slot_finder.py needs to search
    real freebusy data for a candidate slot. No hallucinated times, only computed ones."""

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
    exact_start: Optional[dt.datetime] = None
    """Set only when the user stated one precise preferred moment (simple_datetime with an
    explicit time, e.g. "Tuesday at 2pm") - distinct from earliest_start/hard_deadline, which are
    floors/ceilings on a range of otherwise-equal candidates, not a single preferred instant.
    When set and that exact moment turns out to be free, the dialogue layer presents only that
    slot instead of ranking it alongside nearby alternatives the user never actually asked for -
    found via real usage: asking for an exact time and getting back three options including ones
    30-60 minutes later read as if the assistant hadn't understood the request was specific."""
