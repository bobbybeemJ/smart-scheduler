"""Centralized settings, read from env vars (and .env locally). Render env vars take over
identically in production - no code path differs between local dev and deployed."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    use_mock_llm: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"
    """Tried moving to gemini-3.6-flash and then gemini-3.5-flash after real-usage testing found
    this lite model unreliable on compound sentences (duration/day-part combinations correct
    only ~25-33% of the time; occasional chain-of-thought leaking into structured output fields).
    Both non-lite models scored 6/6 on the same fragile phrases - but BOTH also hit an identical
    hard 429 wall at 20 requests/DAY on this project's free-tier key, not the ~1500 RPD / 15 RPM
    figure commonly quoted for gemini-3.5-flash's free tier elsewhere. Verified this was a real
    daily cap and not a pacing artifact: waited out the exact `retryDelay` the API itself
    reported, then waited 100+ seconds completely untouched (no other calls in between,
    ruling out a sliding-window reset triggered by repeated polling), and still got 429s both
    times, on both models, on different days.

    Back on gemini-flash-lite-latest as the only model with consistently usable free-tier quota.
    Its compound-sentence unreliability is now handled with targeted deterministic fixes in
    app/llm/client.py (duration-extraction regex fallback, raw_phrase reasoning-leak cleanup)
    instead of further prompt iteration or another model switch - see that file's docstrings.
    Re-verify with scripts/sanity/list_gemini_models.py plus a small, paced test batch if this
    changes; exact daily caps aren't documented anywhere queryable in advance."""

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""

    enable_server_stt_fallback: bool = True
    whisper_fallback_model: str = "tiny.en"
    whisper_local_test_model: str = "base.en"

    tts_voice: str = "en-US-AriaNeural"

    # The calendar owner's timezone. Every datetime elsewhere in the app is naive "wall clock"
    # time by design (see app/dateresolve) - this is the one place that boundary gets crossed,
    # since the real Google Calendar API requires timezone-aware timestamps regardless of what
    # timezone the server itself happens to run in (Render's datacenter is not the user's).
    user_timezone: str = "Asia/Kolkata"

    port: int = 8000
    log_level: str = "INFO"


settings = Settings()
