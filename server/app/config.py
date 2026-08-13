"""Typed configuration, loaded once from the environment and `.env`."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": `.env` carries keys this class does not model, and
    # pydantic-settings raises on unknown keys by default.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    # NoDecode: pydantic-settings parses a list field as JSON *in the env source*,
    # before any validator runs, so `CORS_ORIGINS=http://a,http://b` — the form
    # everyone writes — would raise a JSONDecodeError at import, before there is
    # an app to report it. NoDecode hands the raw string to the validator below.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.2-2025-12-11"
    openai_timeout_seconds: float = 60.0

    max_content_bytes: int = 1_000_000  # save path
    max_html_chars: int = 200_000  # AI input
    max_instruction_chars: int = 2_000
    max_context_chars: int = 40_000
    max_history_turns: int = 3

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated_origins(cls, value: object) -> object:
        """Comma-separated is the documented form; a real list (the default, or a
        test passing one in) falls through untouched."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def ai_enabled(self) -> bool:
        """False when the key is absent or still the `.env.example` placeholder.

        A reviewer who runs `cp .env.example .env` and forgets to paste a real key
        must get a clean "AI is not configured" 503, not an authentication 500.
        """
        key = (self.openai_api_key or "").strip()
        return bool(key) and not key.startswith("sk-XXXX")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Deliberately not a module-level singleton: tests need to
    vary the configuration, and an import-time instance cannot be varied."""
    return Settings()
