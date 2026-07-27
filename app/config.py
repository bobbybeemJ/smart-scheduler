"""Centralized settings, read from env vars (and .env locally). Render env vars take over
identically in production - no code path differs between local dev and deployed."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    use_mock_llm: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    """Switched from gemini-flash-lite-latest after real-usage testing found the lite model
    unreliable on compound sentences (duration/day-part combinations correct only ~25-33% of the
    time; occasional chain-of-thought leaking into structured output fields).

    Tried gemini-3.6-flash first (6/6 correct on the same fragile phrases, fastest per call) but
    hit a hard wall: its free tier caps at 20 requests/DAY (not just a per-minute limit), and
    ordinary same-day testing exhausted it outright. gemini-3.5-flash scored the same on
    reliability in direct comparison and had NOT hit a daily cap after a full day's testing at
    the time of switching - re-verify with scripts/sanity/list_gemini_models.py plus a small,
    paced test batch if quota errors reappear, since exact daily caps aren't documented anywhere
    queryable in advance and can only be discovered by actually hitting them."""

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
