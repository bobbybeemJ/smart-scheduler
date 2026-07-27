"""The only place natural language touches an LLM in this whole system. The model's job ends
at producing a schema-valid TemporalExpression - it never computes a date or writes the user
-facing reply (that's app/dialogue/templates.py, plain Python string assembly).

SYSTEM_INSTRUCTION below is deliberately short and purely imperative - no rationale, no history,
no "found via testing X" narration. Every extra sentence there is tokens spent on every single
call, and a verbose, narrative version of this prompt was observed making this free-tier model
(gemini-flash-lite) drift into discussing its own reasoning instead of just producing the
answer - one real response leaked '...as required rule 2 says raw_phrase must not contain...'
directly into a structured output field instead of the field's actual value. Keep this file's
rule rationale in the comments below the string, not inside it:

- Never invent a date/time/duration - null means "ask the user," not "guess."
- A single named day ("tomorrow," a weekday, a specific date) is simple_datetime even when
  phrased with "next week" ("next week on Tuesday" = simple_datetime) - relative_range_with_
  exclusions has no field for "just this one weekday," so a single day put there is silently
  lost and the whole week gets searched instead.
- calendar_arithmetic's ordinal/day_type must reflect exactly what's said, never default to
  whatever combination used to be the only one supported - a schema with only one representable
  answer produces a confidently wrong one instead of an error when the user asks for something
  else. Same for week_offset/month_offset defaulting to a "common" value.
- slot_decision must yield to a fresh request whenever the message states its own new day/time/
  duration, even if it also contains a confirming word like "book" - otherwise the wrong
  (already-offered) slot gets booked instead of the one actually just requested.
- earliest_time must never receive anything but a clock time or null - a named event/person
  belongs in dynamic_buffer's reference_event_name (buffer_source="named_event") instead.
  buffer_source only ever has two valid values; a stale prompt draft once described a third
  ("next_meeting_today") that was never actually added to the schema - schema is the source of
  truth here, always cross-check a rule against the actual Literal/Enum values before trusting it.
- duration_minutes stated BEFORE the date/event ("a 30 minute meeting Tuesday at 2pm") is not
  covered by an example here on purpose: adding explicit leading-duration examples was tried and
  measured 9/9 failures, unchanged from before adding them - this free-tier model just doesn't
  reliably follow that instruction no matter how it's worded. app/llm/client.py's
  _extract_duration_minutes_fallback regexes the raw transcript for a number+unit whenever the
  model leaves duration_minutes null, so this case is covered deterministically instead of by
  more prompt text that was already shown not to help.
- simple_datetime.raw_phrase was a required str until real testing found the assignment's own
  example opening line ("I need to schedule a meeting", no day/time at all) getting classified as
  out_of_scope instead - a required field left the model no legal way to say "nothing stated yet"
  without either inventing text (forbidden by the first rule above) or picking the wrong kind.
  Made Optional in app/schemas.py; resolver.py treats null the same as an unparseable phrase.
"""

from __future__ import annotations

import json
from typing import Optional

SYSTEM_INSTRUCTION = """Extract structured scheduling intent from the user's message into the given schema. Never compute a real calendar date yourself - only extract raw fields (weekday names, hour strings, day/week/month offsets, event names). All date arithmetic happens afterward in Python.

Rules:
- duration_minutes: null unless stated in this message, or the session state shows it's already established for the meeting being continued. Never leave duration wording sitting inside raw_phrase/event_name/anchor fields instead - extract it out. Examples: "next Thursday for 30 minutes" -> duration_minutes=30, raw_phrase="next Thursday". "next Wednesday afternoon for 45 minutes" -> duration_minutes=45, raw_phrase="next Wednesday afternoon" (not raw_phrase="next Wednesday afternoon for 45 minutes").
- Pick exactly one kind. contextual_reference is only for a reference to a previous/habitual meeting by description ("our usual sync-up"), never for a fresh request with its own constraints. A single named day (a weekday, "tomorrow", a specific date) is simple_datetime, even when phrased with "next week" - relative_range_with_exclusions is only for an actual range with no single day named.
- duration_update: only when session state shows an established meeting and the message changes ONLY the duration, nothing else about day/time.
- relative_range_with_exclusions: week_offset is signed (0=this week, 1=next, 2=the week after next, -1=last week, etc - any "N week(s)" phrasing maps here). time_preference (not_too_early/not_too_late) is a vague hour-of-day preference; week_position (early_in_range/late_in_range) is which days of the range to favor. These are independent - set only what's actually implied, leave the other null.
- earliest_time is an HH:MM clock-time floor ("not before 11am", "nothing before 9am", "after 7pm") shared by deadline_before, event_relative, and dynamic_buffer. Set it only when a literal hour is stated or unambiguously implied, never invented - it must always be a clock time or null, never a name (a named event/person belongs in dynamic_buffer's reference_event_name instead).
- calendar_arithmetic: ordinal (first/second/third/fourth/fifth/last) x day_type ("weekday" = any Mon-Fri business day, or a specific weekday name) x month_offset (signed, same idea as week_offset). Fill in exactly what the user said for each part independently - never default to a previously-common combination.
- slot_decision: only when session state's phase is "confirming" and the message is purely reacting to the offered slots (confirm_top / select_index with the 0-based position / reject_all), with no new day/time/duration of its own. A message stating its own new specific day/time/duration is a fresh request instead (its own proper kind), even if it also contains a word like "book".
- out_of_scope: anything that isn't a scheduling request at all - cancellations, unrelated questions, small talk. A message that DOES want to schedule something but names no day/time at all ("I need to schedule a meeting") is simple_datetime with raw_phrase=null, not out_of_scope - out_of_scope means the message itself isn't about scheduling, not that it's missing details.
- dynamic_buffer: earliest_time and buffer_minutes/buffer_source (a floor relative to another event) are independent and can combine, not alternatives. buffer_source is "last_meeting_today" or "named_event" (with reference_event_name set to what was named).
"""


def build_user_content(transcript: str, condensed_state: Optional[dict] = None) -> str:
    state_block = ""
    if condensed_state:
        state_block = f"\n\nCondensed session state so far (JSON): {json.dumps(condensed_state)}"
    return f"User's message: {transcript!r}{state_block}"
