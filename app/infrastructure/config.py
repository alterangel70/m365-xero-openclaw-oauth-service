from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Single settings object loaded once at startup from environment variables
    or a .env file.  Fields without defaults are required; the app will fail
    to start if they are missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Microsoft / Graph ─────────────────────────────────────────────────────
    # client_credentials grant only; no user principal, no browser flow.
    ms_tenant_id: str
    ms_client_id: str
    ms_client_secret: str
    ms_graph_scopes: str = "https://graph.microsoft.com/.default"
    ms_default_connection_id: str = "ms-default"

    # ── Xero ──────────────────────────────────────────────────────────────────
    # Confidential client; client_secret is stored server-side only.
    xero_client_id: str
    xero_client_secret: str
    xero_redirect_uri: str
    xero_scopes: str = (
        "openid profile email accounting.transactions accounting.contacts"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── Application ───────────────────────────────────────────────────────────
    app_base_url: str = "http://localhost:8000"
    refresh_buffer_seconds: int = 300
    oauth_state_ttl_seconds: int = 600
    idempotency_ttl_seconds: int = 86400
    log_level: str = "INFO"

    # ── Seq (structured logging) ─────────────────────────────────────────────
    # When seq_enabled=true the app ships log events to the Seq ingest endpoint.
    # All four variables are optional; Seq integration is off by default.
    seq_enabled: bool = False
    seq_url: str = ""
    seq_api_key: str = ""
    seq_min_level: str = "INFO"

    # ── Security ──────────────────────────────────────────────────────────────
    internal_api_key: str

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        upper = v.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    @field_validator("seq_min_level", mode="before")
    @classmethod
    def normalise_seq_min_level(cls, v: str) -> str:
        upper = v.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(f"seq_min_level must be one of {allowed}, got {v!r}")
        return upper


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.  Called at import time only once."""
    return Settings()
