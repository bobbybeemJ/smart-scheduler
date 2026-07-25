"""Centralized settings, read from env vars (and .env locally). Render env vars take over
identically in production - no code path differs between local dev and deployed."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    use_mock_llm: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"

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
