"""Canned structured responses, one per scenario from the assignment brief (plus the extra
SimpleDateTime case) - lets the entire pipeline be exercised with USE_MOCK_LLM=true at zero
Gemini token cost. Matched by keyword, not exact string, so minor phrasing variants still hit."""

from __future__ import annotations

from pydantic import TypeAdapter

from app.schemas import TemporalExpression

_ADAPTER: TypeAdapter = TypeAdapter(TemporalExpression)

# keyword -> canned raw JSON-shaped dict, checked in order (first match wins)
_CANNED_RESPONSES: list[tuple[str, dict]] = [
    (
        "flight",
        {
            "kind": "deadline_before",
            "duration_minutes": 45,
            "anchor_weekday": "Friday",
            "anchor_time": "18:00",
            "buffer_minutes": 0,
        },
    ),
    (
        "kick-off",
        {
            "kind": "event_relative",
            "duration_minutes": 15,
            "event_name": "Project Alpha Kick-off",
            "offset_days_min": 1,
            "offset_days_max": 2,
        },
    ),
    (
        "last weekday",
        {
            "kind": "calendar_arithmetic",
            "duration_minutes": 60,
            "expression": "last_weekday_of_month",
        },
    ),
    (
        "not on wednesday",
        {
            "kind": "relative_range_with_exclusions",
            "duration_minutes": None,
            "range": "next_week",
            "exclude_weekdays": ["Wednesday"],
            "time_preference": "not_too_early",
        },
    ),
    (
        "late next week",
        {
            "kind": "relative_range_with_exclusions",
            "duration_minutes": None,
            "range": "next_week",
            "week_position": "late_in_range",
        },
    ),
    (
        "usual sync-up",
        {
            "kind": "contextual_reference",
            "reference": "our usual sync-up",
        },
    ),
    (
        "decompress",
        {
            "kind": "dynamic_buffer",
            "duration_minutes": None,
            "after_time": "19:00",
            "buffer_minutes": 60,
        },
    ),
    (
        "2pm",
        {
            "kind": "simple_datetime",
            "duration_minutes": 30,
            "raw_phrase": "Tuesday at 2pm",
        },
    ),
    (
        "full hour",
        {
            "kind": "duration_update",
            "duration_minutes": 60,
        },
    ),
]


def get_mock_intent(transcript: str) -> TemporalExpression:
    lowered = transcript.lower()
    for keyword, canned in _CANNED_RESPONSES:
        if keyword in lowered:
            return _ADAPTER.validate_python(canned)
    raise LookupError(
        f"No mock response defined for {transcript!r}. Add one to mock_responses.py, or set "
        "USE_MOCK_LLM=false to use the real Gemini call."
    )
