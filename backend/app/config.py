"""Environment-based configuration. Every setting can be overridden by an environment variable."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR / ".env",), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://reception:reception@localhost:5434/reception"

    # Agent brain: "gemini" (the real model behind the gateway) or "rules" (deterministic reference policy,
    # used by the offline evaluation and as a no-key fallback for demos).
    agent_brain: Literal["gemini", "rules"] = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] = "minimal"
    agent_temperature: float = Field(default=0.3, ge=0, le=2)
    agent_max_output_tokens: int = Field(default=400, ge=64, le=4096)
    agent_history_turns: int = Field(default=12, ge=2, le=50)
    model_timeout_seconds: float = Field(default=20, ge=2, le=120)
    model_max_attempts: int = Field(default=2, ge=1, le=5)
    daily_model_call_limit: int = Field(default=300, ge=0)
    # USD per million tokens, used to fill model_calls.cost_usd. Unset on the free tier, so cost stays null
    # rather than showing an invented number.
    model_price_in_per_mtok: float | None = None
    model_price_out_per_mtok: float | None = None

    # The business
    business_name: str = "Larkspur Salon"
    business_timezone: str = "Asia/Kolkata"
    default_country_code: str = "+91"
    slot_step_minutes: int = Field(default=15, ge=5, le=60)
    booking_horizon_days: int = Field(default=21, ge=1, le=90)
    min_notice_minutes: int = Field(default=60, ge=0)
    max_failed_turns: int = Field(default=2, ge=1, le=5)
    currency_word: str = "rupees"

    # Browser access
    frontend_origin: str = "http://localhost:5173"
    demo_access_code: str | None = None
    turns_per_minute_per_call: int = Field(default=20, ge=1)
    calls_per_hour_per_client: int = Field(default=30, ge=1)

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        # Managed hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver name.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origin.split(",") if origin.strip()]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.business_timezone)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
