from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Gemini
    gemini_api_key: str
    gemini_live_api_key: str

    # Google Calendar OAuth
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/auth/callback"

    # LangSmith (activated by env vars — no instrumentation code needed)
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "smart-scheduler"

    # User
    user_timezone: str = "Asia/Kolkata"

    # Conflict escalation
    escalation_email: str = ""

    # SMTP for escalation emails (all optional — gracefully skipped if not set)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
