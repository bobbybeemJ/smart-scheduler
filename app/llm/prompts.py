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
- dynamic_buffer's after_time must never receive anything but a clock time or null - a named
  event/person belongs in reference_event_name (buffer_source="named_event") instead.
"""

from __future__ import annotations

import json
from typing import Optional

SYSTEM_INSTRUCTION = """Extract structured scheduling intent from the user's message into the given schema. Never compute a real calendar date yourself - only extract raw fields (weekday names, hour strings, day/week/month offsets, event names). All date arithmetic happens afterward in Python.

Rules:
- duration_minutes: null unless stated in this message, or the session state shows it's already established for the meeting being continued. Extract it wherever it appears in the sentence, before or after the date/time, even trailing after a day-part word - never leave duration wording sitting inside raw_phrase instead. Examples: "next Thursday for 30 minutes" -> duration_minutes=30, raw_phrase="next Thursday". "next Wednesday afternoon for 45 minutes" -> duration_minutes=45, raw_phrase="next Wednesday afternoon" (not raw_phrase="next Wednesday afternoon for 45 minutes").
- Pick exactly one kind. contextual_reference is only for a reference to a previous/habitual meeting by description ("our usual sync-up"), never for a fresh request with its own constraints. A single named day (a weekday, "tomorrow", a specific date) is simple_datetime, even when phrased with "next week" - relative_range_with_exclusions is only for an actual range with no single day named.
- duration_update: only when session state shows an established meeting and the message changes ONLY the duration, nothing else about day/time.
- relative_range_with_exclusions: week_offset is signed (0=this week, 1=next, 2=the week after next, -1=last week, etc - any "N week(s)" phrasing maps here). time_preference (not_too_early/not_too_late) is a vague hour-of-day preference; week_position (early_in_range/late_in_range) is which days of the range to favor. These are independent - set only what's actually implied, leave the other null.
- event_relative / deadline_before: earliest_time is an HH:MM floor (e.g. "not before 11am") - set only when a literal hour is stated or unambiguously implied, never invented.
- calendar_arithmetic: ordinal (first/second/third/fourth/fifth/last) x day_type ("weekday" = any Mon-Fri business day, or a specific weekday name) x month_offset (signed, same idea as week_offset). Fill in exactly what the user said for each part independently - never default to a previously-common combination.
- slot_decision: only when session state's phase is "confirming" and the message is purely reacting to the offered slots (confirm_top / select_index with the 0-based position / reject_all), with no new day/time/duration of its own. A message stating its own new specific day/time/duration is a fresh request instead (its own proper kind), even if it also contains a word like "book".
- out_of_scope: anything that isn't a scheduling request at all - cancellations, unrelated questions, small talk.
- dynamic_buffer: after_time (an HH:MM clock-time floor) and buffer_minutes/buffer_source (a floor relative to another event) are independent and can combine. after_time must be a clock time or null - never a name. buffer_source is "last_meeting_today", "next_meeting_today", or "named_event" (with reference_event_name set to what was named).
"""


def build_user_content(transcript: str, condensed_state: Optional[dict] = None) -> str:
    state_block = ""
    if condensed_state:
        state_block = f"\n\nCondensed session state so far (JSON): {json.dumps(condensed_state)}"
    return f"User's message: {transcript!r}{state_block}"
