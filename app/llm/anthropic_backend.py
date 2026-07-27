"""Claude backend for extract_intent() - see app/llm/client.py for the provider dispatch and
app/llm/gemini_backend.py for the original Gemini backend.

Uses one Claude tool PER kind (not one flat tool with every field from every kind) so each
tool's input_schema only exposes the fields that kind's Pydantic model actually has - this
mirrors what Gemini's response_schema does natively via a discriminated union. A first prototype
using one flat tool with all ~20 fields let the model put fields on the wrong kind (e.g.
anchor_weekday/time_preference on simple_datetime, which doesn't have those fields at all)
because nothing in the schema said those fields didn't belong there. Splitting into 10 per-kind
tools with tool_choice="any" (forces exactly one tool call, model's choice which) fixed that
completely - tested clean across it.

Comparison vs gemini-flash-lite-latest (2026-07-27, same phrases that broke Gemini this
session): 14/15 clean on the first try, INCLUDING every one of the worst Gemini bugs -
"a 30 minute meeting Tuesday at 2pm" (the leading-duration bug, 9/9 failures on Gemini even
after adding prompt examples for it), "tomorrow at 3pm for 30 minutes" (duration leaking into
raw_phrase), "I need to schedule a meeting" (misclassified as out_of_scope on Gemini) - all
correct with ZERO regex fallbacks needed on the first pass. The one apparent miss (guessing
select_index=0 for "book it for 12:00 p.m." during confirmation, with no actual offered-candidate
times in context) isn't a real production risk: app/dialogue/manager.py's _parse_slot_selection
and _resolve_explicit_time_during_confirmation already intercept that exact judgment call
deterministically before extract_intent() is ever called, regardless of provider - matching a
stated time against actual offered candidates belongs in code, not a model's guess, even a good
model's, since neither backend's tool call ever sees the actual candidate clock times. A second,
broader battery (18 scenarios covering all 6 assignment hard cases plus basic/multi-turn flows)
then scored 18/18 clean.

Follow-up spot testing did find ONE real, if much rarer, overlap with Gemini's failure modes:
given a condensed_state block (not just a bare message), Claude occasionally - roughly 1 in 5-10
calls on "Tuesday at 3pm for 30 minutes" - still leaves the duration wording sitting in
raw_phrase alongside a correctly-extracted duration_minutes, the same leak gemini_backend.py
hits far more often. Unlike gemini_backend.py's heavier reconstruction fallbacks (corruption
detection, rebuilding raw_phrase from the transcript), this one specific cleanup
(text_cleanup.strip_duration_mention) is cheap and provider-agnostic enough to apply here too -
it only ever removes text redundant with a duration_minutes value already known, never guesses
anything, so there's no real cost to keeping it even though Claude needs it far less often.

SYSTEM_INSTRUCTION below is deliberately much shorter than gemini_backend's prompt (no per-tool
"tool choice guide" repeating what each tool's own `description` already says, no exhaustive
worked examples for every phrasing shape) - Claude didn't need the Gemini-style hand-holding a
side-by-side test confirmed (14/15 clean with this exact short prompt, the one miss being an
unrelated resolver bug fixed in app/dateresolve/helpers.py, not a prompt gap). Only the handful
of genuinely cross-cutting distinctions that no single tool description can carry on its own
(simple_datetime vs relative_range's boundary, out_of_scope vs an incomplete-but-real request,
etc.) are called out explicitly.

One schema-design lesson from building the per-kind tools: an early draft marked
schedule_deadline_before's buffer_minutes as required (mirroring its non-optional appearance in
early testing), which made the model invent a value (copying duration_minutes) whenever no
actual buffer was stated - the same "required field forces invention" trap already documented on
SimpleDateTime.raw_phrase in app/schemas.py. Fixed by making it optional in the tool schema, with
the Pydantic model's own default (0) applied in _build_intent when omitted.

Cost: ~2400 input + ~65 output tokens per call on claude-haiku-4-5 (mostly the 10 tool schema
definitions), roughly $0.003/call at Haiku 4.5 pricing ($1/$5 per MTok in/out) - about $3 per
1000 full conversations. Prompt caching (system + tools are static across every call) would cut
the ~2400-token overhead further but hasn't been added yet - the cost is already low enough that
this is an optional follow-up, not a blocker."""

from __future__ import annotations

import json
from typing import Optional

import anthropic
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.errors import LLMExtractionError
from app.llm.text_cleanup import strip_duration_mention
from app.schemas import (
    CalendarArithmetic,
    ContextualReference,
    DeadlineBefore,
    DurationUpdate,
    DynamicBuffer,
    EventRelative,
    OutOfScope,
    RelativeRangeWithExclusions,
    SimpleDateTime,
    SlotDecision,
    TemporalExpression,
)

_client: Optional[anthropic.Anthropic] = None

MAX_TOKENS = 512

SYSTEM_INSTRUCTION = """Extract structured scheduling intent by calling exactly one tool matching what the user's message actually asks for. Never compute a calendar date yourself, and never invent a date/time/duration that wasn't stated - leave a field null instead of guessing.

A few distinctions worth getting right:
- A single named day/time (a weekday, "tomorrow", a specific date) is schedule_simple_datetime, even phrased with "next week" ("next week on Tuesday") - schedule_relative_range is only for an actual range with no single day named. Put the whole day/time phrase as one string in raw_phrase; if the user wants to schedule something but hasn't stated a day/time yet, call schedule_simple_datetime with raw_phrase left out rather than out_of_scope.
- out_of_scope is only for messages that aren't about scheduling at all (cancellations, small talk, unrelated questions) - not for a legitimate but incomplete scheduling request.
- schedule_contextual_reference is only for referring to a past/habitual meeting by description ("our usual sync-up"), never a fresh request with its own constraints.
- decide_on_offered_slots and update_duration only apply when the session state shows there's actually something in progress to react to or update.
- duration_minutes can appear anywhere in the sentence (before, after, or mixed in with the date/event) - extract it into its own field regardless of position, never leave it sitting inside another field's text.
"""

TOOLS = [
    {
        "name": "schedule_deadline_before",
        "description": "An explicit deadline the meeting must finish before.",
        "input_schema": {
            "type": "object",
            "properties": {
                "anchor_weekday": {"type": "string", "description": "the weekday name of the deadline"},
                "anchor_time": {"type": ["string", "null"], "description": "HH:MM 24h, or null if not stated"},
                "buffer_minutes": {"type": ["integer", "null"], "description": "extra minutes of safety margin stated before the deadline, e.g. 'at least 30 minutes before my flight' - null/0 if no such margin was stated, do not confuse with duration_minutes"},
                "earliest_time": {"type": ["string", "null"], "description": "HH:MM clock-time floor, or null"},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": ["anchor_weekday"],
        },
    },
    {
        "name": "schedule_event_relative",
        "description": "Relative to a named calendar event, by a day offset range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string"},
                "offset_days_min": {"type": "integer"},
                "offset_days_max": {"type": "integer"},
                "earliest_time": {"type": ["string", "null"]},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": ["event_name", "offset_days_min", "offset_days_max"],
        },
    },
    {
        "name": "schedule_calendar_arithmetic",
        "description": "The Nth weekday-or-business-day of a given month.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ordinal": {"type": "string", "enum": ["first", "second", "third", "fourth", "fifth", "last"]},
                "day_type": {
                    "type": "string",
                    "enum": ["weekday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                    "description": "'weekday' for any Mon-Fri business day, or a specific weekday name",
                },
                "month_offset": {"type": ["integer", "null"], "description": "0=this month, 1=next month, etc - null defaults to 0"},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": ["ordinal", "day_type"],
        },
    },
    {
        "name": "schedule_relative_range",
        "description": "An actual range of days to search, with no single day named.",
        "input_schema": {
            "type": "object",
            "properties": {
                "week_offset": {"type": ["integer", "null"], "description": "0=this week, 1=next week, etc - null defaults to 1"},
                "exclude_weekdays": {"type": ["array", "null"], "items": {"type": "string"}},
                "time_preference": {"type": ["string", "null"], "enum": ["not_too_early", "not_too_late", None]},
                "week_position": {"type": ["string", "null"], "enum": ["early_in_range", "late_in_range", None]},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": [],
        },
    },
    {
        "name": "schedule_contextual_reference",
        "description": "Reference to a previous/habitual meeting by description, e.g. 'our usual sync-up'.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"],
        },
    },
    {
        "name": "schedule_dynamic_buffer",
        "description": "A floor relative to another event or today's last meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "buffer_minutes": {"type": "integer", "description": "the stated buffer duration, e.g. 'an hour to decompress' -> 60"},
                "buffer_source": {"type": ["string", "null"], "enum": ["last_meeting_today", "named_event", None], "description": "null defaults to last_meeting_today"},
                "reference_event_name": {"type": ["string", "null"]},
                "earliest_time": {"type": ["string", "null"]},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": ["buffer_minutes"],
        },
    },
    {
        "name": "schedule_simple_datetime",
        "description": "A single named day/time, or a vague scheduling request with no day/time stated yet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_phrase": {"type": ["string", "null"], "description": "the ENTIRE day/time phrase as one string, or null if none stated yet"},
                "duration_minutes": {"type": ["integer", "null"]},
            },
            "required": [],
        },
    },
    {
        "name": "update_duration",
        "description": "A correction to an already-established duration, nothing else about day/time changed.",
        "input_schema": {
            "type": "object",
            "properties": {"duration_minutes": {"type": "integer"}},
            "required": ["duration_minutes"],
        },
    },
    {
        "name": "decide_on_offered_slots",
        "description": "Reacting to offered slots during confirmation - confirm/select/reject.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["confirm_top", "select_index", "reject_all"]},
                "selected_index": {"type": ["integer", "null"]},
            },
            "required": ["decision"],
        },
    },
    {
        "name": "out_of_scope",
        "description": "Not a scheduling request at all.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _build_intent(tool_name: str, raw: dict) -> TemporalExpression:
    """Un-flattens a (tool_name, input) tool_use call back into the matching Pydantic subclass -
    the inverse of the per-kind tool split above. Field-by-field (not **raw) so each kind's own
    defaults (schemas.py is the source of truth) apply consistently whether Claude omits an
    optional field or explicitly sends it as null - both mean "not stated" here."""
    if tool_name == "schedule_deadline_before":
        return DeadlineBefore(
            anchor_weekday=raw["anchor_weekday"],
            anchor_time=raw.get("anchor_time"),
            buffer_minutes=raw.get("buffer_minutes") or 0,
            earliest_time=raw.get("earliest_time"),
            duration_minutes=raw.get("duration_minutes"),
        )
    if tool_name == "schedule_event_relative":
        return EventRelative(
            event_name=raw["event_name"],
            offset_days_min=raw["offset_days_min"],
            offset_days_max=raw["offset_days_max"],
            earliest_time=raw.get("earliest_time"),
            duration_minutes=raw.get("duration_minutes"),
        )
    if tool_name == "schedule_calendar_arithmetic":
        return CalendarArithmetic(
            ordinal=raw["ordinal"],
            day_type=raw["day_type"],
            month_offset=raw.get("month_offset") or 0,
            duration_minutes=raw.get("duration_minutes"),
        )
    if tool_name == "schedule_relative_range":
        return RelativeRangeWithExclusions(
            week_offset=raw.get("week_offset") if raw.get("week_offset") is not None else 1,
            exclude_weekdays=raw.get("exclude_weekdays") or [],
            time_preference=raw.get("time_preference"),
            week_position=raw.get("week_position"),
            duration_minutes=raw.get("duration_minutes"),
        )
    if tool_name == "schedule_contextual_reference":
        return ContextualReference(reference=raw["reference"])
    if tool_name == "schedule_dynamic_buffer":
        return DynamicBuffer(
            buffer_minutes=raw["buffer_minutes"],
            buffer_source=raw.get("buffer_source") or "last_meeting_today",
            reference_event_name=raw.get("reference_event_name"),
            earliest_time=raw.get("earliest_time"),
            duration_minutes=raw.get("duration_minutes"),
        )
    if tool_name == "schedule_simple_datetime":
        return SimpleDateTime(raw_phrase=raw.get("raw_phrase"), duration_minutes=raw.get("duration_minutes"))
    if tool_name == "update_duration":
        return DurationUpdate(duration_minutes=raw["duration_minutes"])
    if tool_name == "decide_on_offered_slots":
        return SlotDecision(decision=raw["decision"], selected_index=raw.get("selected_index"))
    if tool_name == "out_of_scope":
        return OutOfScope()
    raise LLMExtractionError(f"Claude called an unknown tool: {tool_name!r}")


def _is_transient_anthropic_error(exc: BaseException) -> bool:
    """Retry on rate limits and server errors - never on 4xx validation/auth failures, which
    won't succeed on retry and would just add latency for nothing. Mirrors
    gemini_backend._is_transient_gemini_error's same reasoning for the other provider."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return 500 <= exc.status_code < 600
    return isinstance(exc, anthropic.APIConnectionError)


_retry_anthropic = retry(
    retry=retry_if_exception(_is_transient_anthropic_error),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=2),
    reraise=True,
)


def _get_client() -> anthropic.Anthropic:
    """Singleton, built once per process - avoids repeating the client-setup cost on every turn
    (see the plan's latency optimization backlog; mirrors gemini_backend._get_client)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_user_content(transcript: str, condensed_state: Optional[dict]) -> str:
    state_block = f"\n\nCondensed session state so far (JSON): {json.dumps(condensed_state)}" if condensed_state else ""
    return f"User's message: {transcript!r}{state_block}"


@_retry_anthropic
def _call_claude(transcript: str, condensed_state: Optional[dict]):
    client = _get_client()
    return client.messages.create(
        model=settings.anthropic_model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_INSTRUCTION,
        tools=TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": _build_user_content(transcript, condensed_state)}],
    )


def extract_intent(transcript: str, condensed_state: Optional[dict] = None) -> TemporalExpression:
    try:
        message = _call_claude(transcript, condensed_state)
    except Exception as exc:  # network error, quota error, etc. (after retries are exhausted)
        raise LLMExtractionError(f"Claude call failed: {exc}") from exc

    tool_use = next((block for block in message.content if block.type == "tool_use"), None)
    if tool_use is None:
        raise LLMExtractionError(f"Claude response had no tool_use block: {message.content!r}")

    try:
        intent = _build_intent(tool_use.name, tool_use.input)
    except (KeyError, ValueError) as exc:
        raise LLMExtractionError(f"Claude tool_use input didn't match {tool_use.name}'s schema: {tool_use.input!r} ({exc})") from exc

    if isinstance(intent, SimpleDateTime) and intent.raw_phrase is not None:
        stripped_phrase = strip_duration_mention(intent.raw_phrase)
        if stripped_phrase and stripped_phrase != intent.raw_phrase:
            intent.raw_phrase = stripped_phrase
    return intent
