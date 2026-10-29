from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    portal_base_url: str = Field(
        default="https://ops.urja.internal",
        alias="PORTAL_BASE_URL",
        description="Base URL for Urja Meter Ops legacy portal",
    )
    portal_username: str = Field(
        default="",
        alias="PORTAL_USERNAME",
        description="Portal login email",
    )
    portal_password: str = Field(
        default="",
        alias="PORTAL_PASSWORD",
        description="Portal login password",
    )

    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    metadata_cache_ttl_seconds: int = Field(
        default=900,  # 15 minutes
        alias="METADATA_CACHE_TTL_SECONDS",
        description="TTL for meter metadata cache in seconds",
    )
    consumption_cache_ttl_seconds: int = Field(
        default=60,  # 60 seconds
        alias="CONSUMPTION_CACHE_TTL_SECONDS",
        description="TTL for meter consumption cache in seconds",
    )

    portal_max_concurrency: int = Field(
        default=4,
        alias="PORTAL_MAX_CONCURRENCY",
        description="Maximum concurrent outgoing requests to the legacy portal",
    )
    portal_request_delay_seconds: float = Field(
        default=0.05,
        alias="PORTAL_REQUEST_DELAY_SECONDS",
        description="Small politeness delay in seconds between consecutive portal requests",
    )
    portal_timeout_seconds: float = Field(
        default=10.0,
        alias="PORTAL_TIMEOUT_SECONDS",
        description="Timeout for outgoing portal HTTP requests",
    )
    portal_max_retries: int = Field(
        default=3,
        alias="PORTAL_MAX_RETRIES",
        description="Maximum retries for transient portal errors",
    )
    session_proactive_refresh_seconds: int = Field(
        default=3000,  # ~50 minutes
        alias="SESSION_PROACTIVE_REFRESH_SECONDS",
        description="Seconds after which proactive re-login is triggered before 1h expiry",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
