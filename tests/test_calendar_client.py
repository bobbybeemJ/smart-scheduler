"""Exercises app.calendar_client.client's wrappers against a mocked Google API service object -
no real network, no real credentials needed. Also locks in the timezone handling: every
outbound call must carry a timezone-aware timestamp (Asia/Kolkata, +05:30, no DST), and every
inbound response must come back stripped to naive local time."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from app.calendar_client import client as calendar_client


@pytest.fixture(autouse=True)
def _clear_freebusy_cache():
    # The freebusy cache is module-level (process-lifetime, not per-session - see client.py's
    # own docstring on why that's safe for this single-tenant app), so it persists across tests
    # in this file unless reset - without this, a later test reusing the same window as an
    # earlier one would silently get that earlier test's mocked response instead of its own.
    calendar_client._freebusy_cache.clear()
    yield
    calendar_client._freebusy_cache.clear()


def _mock_service():
    return MagicMock()


def test_list_calendars_parses_response():
    service = _mock_service()
    service.calendarList().list().execute.return_value = {"items": [{"summary": "Test Cal", "id": "abc"}]}

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.list_calendars()

    assert result == [{"summary": "Test Cal", "id": "abc"}]


def test_find_event_by_name_returns_none_when_nothing_found():
    service = _mock_service()
    service.events().list().execute.return_value = {"items": []}

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.find_event_by_name("Nonexistent Event")

    assert result is None


def test_find_event_by_name_parses_response_to_naive_local_time():
    service = _mock_service()
    service.events().list().execute.return_value = {
        "items": [
            {
                "id": "evt1",
                "summary": "Project Alpha Kick-off",
                "start": {"dateTime": "2026-07-20T10:00:00+05:30"},
                "end": {"dateTime": "2026-07-20T11:00:00+05:30"},
            }
        ]
    }

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.find_event_by_name("Project Alpha Kick-off")

    assert result["start"] == dt.datetime(2026, 7, 20, 10, 0)
    assert result["start"].tzinfo is None  # stripped back to naive local, not left aware
    assert result["end"] == dt.datetime(2026, 7, 20, 11, 0)


def test_find_event_by_name_sends_timezone_aware_time_bounds():
    service = _mock_service()
    service.events().list().execute.return_value = {"items": []}

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        calendar_client.find_event_by_name(
            "X", time_min=dt.datetime(2026, 7, 20, 0, 0), time_max=dt.datetime(2026, 7, 22, 0, 0)
        )

    _, kwargs = service.events().list.call_args
    assert kwargs["timeMin"].endswith("+05:30")
    assert kwargs["timeMax"].endswith("+05:30")


def test_freebusy_sends_timezone_aware_bounds_and_parses_busy_periods_to_naive_local():
    service = _mock_service()
    service.freebusy().query().execute.return_value = {
        "calendars": {
            "primary": {
                "busy": [{"start": "2026-07-28T14:00:00+05:30", "end": "2026-07-28T14:30:00+05:30"}],
            }
        }
    }

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.freebusy(dt.datetime(2026, 7, 28, 9, 0), dt.datetime(2026, 7, 28, 17, 0))

    _, kwargs = service.freebusy().query.call_args
    assert kwargs["body"]["timeMin"].endswith("+05:30")

    assert result == [{"start": dt.datetime(2026, 7, 28, 14, 0), "end": dt.datetime(2026, 7, 28, 14, 30)}]
    assert result[0]["start"].tzinfo is None


def test_freebusy_reuses_cached_result_for_the_identical_window():
    service = _mock_service()
    service.freebusy().query().execute.return_value = {
        "calendars": {"primary": {"busy": [{"start": "2026-07-28T14:00:00+05:30", "end": "2026-07-28T14:30:00+05:30"}]}}
    }
    start, end = dt.datetime(2026, 7, 28, 9, 0), dt.datetime(2026, 7, 28, 17, 0)

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        first = calendar_client.freebusy(start, end)
        second = calendar_client.freebusy(start, end)

    assert first == second
    assert service.freebusy().query().execute.call_count == 1  # second call was served from cache


def test_freebusy_does_not_reuse_cache_for_a_different_window():
    service = _mock_service()
    service.freebusy().query().execute.return_value = {"calendars": {"primary": {"busy": []}}}

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        calendar_client.freebusy(dt.datetime(2026, 7, 28, 9, 0), dt.datetime(2026, 7, 28, 17, 0))
        calendar_client.freebusy(dt.datetime(2026, 7, 29, 9, 0), dt.datetime(2026, 7, 29, 17, 0))

    assert service.freebusy().query().execute.call_count == 2


def test_insert_event_invalidates_the_freebusy_cache():
    service = _mock_service()
    service.freebusy().query().execute.return_value = {"calendars": {"primary": {"busy": []}}}
    service.events().insert().execute.return_value = {"id": "new-event-id"}
    start, end = dt.datetime(2026, 7, 28, 9, 0), dt.datetime(2026, 7, 28, 17, 0)

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        calendar_client.freebusy(start, end)  # populates the cache
        calendar_client.insert_event("Meeting", dt.datetime(2026, 7, 28, 14, 0), dt.datetime(2026, 7, 28, 14, 30))
        calendar_client.freebusy(start, end)  # must hit the API again, not the now-stale cache

    assert service.freebusy().query().execute.call_count == 2


def test_insert_event_sends_timezone_field_alongside_datetime():
    service = _mock_service()
    service.events().insert().execute.return_value = {"id": "new-event-id"}

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.insert_event(
            "Test Meeting", dt.datetime(2026, 7, 28, 14, 0), dt.datetime(2026, 7, 28, 14, 30)
        )

    _, kwargs = service.events().insert.call_args
    assert kwargs["body"]["start"]["timeZone"] == "Asia/Kolkata"
    assert kwargs["body"]["start"]["dateTime"].endswith("+05:30")
    assert kwargs["body"]["summary"] == "Test Meeting"
    assert result == {"id": "new-event-id"}


def test_delete_event_calls_the_api_with_the_right_id():
    service = _mock_service()

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        calendar_client.delete_event("some-event-id")

    _, kwargs = service.events().delete.call_args
    assert kwargs["eventId"] == "some-event-id"


def test_find_last_event_of_day_returns_the_last_item():
    service = _mock_service()
    service.events().list().execute.return_value = {
        "items": [
            {"id": "1", "summary": "Morning standup", "start": {"dateTime": "2026-07-22T09:00:00+05:30"}, "end": {"dateTime": "2026-07-22T09:15:00+05:30"}},
            {"id": "2", "summary": "1:1", "start": {"dateTime": "2026-07-22T17:00:00+05:30"}, "end": {"dateTime": "2026-07-22T17:30:00+05:30"}},
        ]
    }

    with patch.object(calendar_client, "get_calendar_service", return_value=service):
        result = calendar_client.find_last_event_of_day(dt.date(2026, 7, 22))

    assert result["summary"] == "1:1"
    assert result["end"] == dt.datetime(2026, 7, 22, 17, 30)
