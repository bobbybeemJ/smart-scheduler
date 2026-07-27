"""tenacity was a declared dependency from the start of this project's tech stack but had never
actually been wired up anywhere - these tests confirm the retry logic added later actually
retries transient failures and gives up immediately on permanent ones, for the Gemini, Claude,
and Calendar API call sites."""

from unittest.mock import MagicMock

import anthropic
from googleapiclient.errors import HttpError

from app.calendar_client.client import _is_transient_http_error, _retry_transient
from app.llm.anthropic_backend import _is_transient_anthropic_error, _retry_anthropic
from app.llm.gemini_backend import _is_transient_gemini_error, _retry_gemini


def _fake_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error body")


def _fake_anthropic_status_error(status: int) -> anthropic.APIStatusError:
    import httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req)
    if status == 429:
        return anthropic.RateLimitError("rate limited", response=resp, body=None)
    return anthropic.APIStatusError("server error", response=resp, body=None)


def test_gemini_429_is_transient():
    class FakeError(Exception):
        code = 429

    assert _is_transient_gemini_error(FakeError())


def test_gemini_500_is_transient():
    class FakeError(Exception):
        code = 503

    assert _is_transient_gemini_error(FakeError())


def test_gemini_400_is_not_transient():
    class FakeError(Exception):
        code = 400

    assert not _is_transient_gemini_error(FakeError())


def test_anthropic_429_is_transient():
    assert _is_transient_anthropic_error(_fake_anthropic_status_error(429))


def test_anthropic_500_is_transient():
    assert _is_transient_anthropic_error(_fake_anthropic_status_error(503))


def test_anthropic_400_is_not_transient():
    assert not _is_transient_anthropic_error(_fake_anthropic_status_error(400))


def test_anthropic_retry_actually_retries_and_succeeds_on_transient_failure():
    calls = {"count": 0}

    @_retry_anthropic
    def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            raise _fake_anthropic_status_error(429)
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 2  # failed once, succeeded on retry


def test_anthropic_retry_gives_up_immediately_on_non_transient_failure():
    calls = {"count": 0}

    @_retry_anthropic
    def always_fails_permanently():
        calls["count"] += 1
        raise _fake_anthropic_status_error(400)

    try:
        always_fails_permanently()
        assert False, "expected the permanent error to propagate"
    except Exception:
        pass
    assert calls["count"] == 1  # no retry attempted for a non-transient error


def test_calendar_429_and_5xx_are_transient():
    assert _is_transient_http_error(_fake_http_error(429))
    assert _is_transient_http_error(_fake_http_error(503))


def test_calendar_404_is_not_transient():
    assert not _is_transient_http_error(_fake_http_error(404))


def test_retry_actually_retries_and_succeeds_on_transient_failure():
    calls = {"count": 0}

    class FakeError(Exception):
        code = 429

    @_retry_gemini
    def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            raise FakeError()
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 2  # failed once, succeeded on retry


def test_retry_gives_up_immediately_on_non_transient_failure():
    calls = {"count": 0}

    class FakeError(Exception):
        code = 400

    @_retry_gemini
    def always_fails_permanently():
        calls["count"] += 1
        raise FakeError()

    try:
        always_fails_permanently()
        assert False, "expected the permanent error to propagate"
    except Exception:
        pass
    assert calls["count"] == 1  # no retry attempted for a non-transient error


def test_calendar_retry_actually_retries_transient_failures():
    calls = {"count": 0}

    @_retry_transient
    def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            raise _fake_http_error(503)
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 2
