"""Typed configuration, loaded once from the environment and `.env`."""

from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": `.env` carries keys this class does not model, and
    # pydantic-settings raises on unknown keys by default.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    # NoDecode: pydantic-settings parses a list field as JSON before any validator
    # runs, so `CORS_ORIGINS=http://a,http://b` — the form everyone writes — would
    # raise at import. NoDecode hands the raw string to the validator below.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # SecretStr, not str: any repr of this object — a log line, a validation error,
    # a debugger frame — would otherwise print the live key in full.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.2-2025-12-11"

    max_content_bytes: int = 1_000_000  # save path
    max_html_chars: int = 200_000  # AI input
    max_instruction_chars: int = 2_000
    max_context_chars: int = 40_000
    # The Q&A branch's context budget, deliberately ABOVE `max_html_chars`: rendering a
    # document as context costs ~1.0-1.05x its HTML, so every document this app accepts
    # is read whole and retrieval is a safety net rather than the normal path. Measured
    # on a 196,395-character patent: at 120,000 two sections were dropped and the call
    # took a median 2.8 s; at 220,000 nothing was dropped and it took a median 1.8 s.
    # Bigger is faster here — elision markers make the model work harder than prose does.
    max_answer_context_chars: int = 220_000
    # The EDITING branch's specification budget. Defaulted at `max_html_chars` rather
    # than below it, for the same reason and with the same effect: the sections are a
    # strict subset of the HTML they were parsed from, so every document this app accepts
    # has its whole specification read, `build_spec` stays on tier 1, and the rendered
    # text is a pure function of the document — which is what keeps the prompt prefix
    # cacheable from turn to turn. Below this, retrieval is a safety net, not the path.
    max_spec_context_chars: int = 200_000
    max_history_turns: int = 3

    # The whole prompt, every block of it. Not enforced at runtime — the per-view
    # ceilings above already bound each block, and clamping here would mean silently
    # dropping context nothing had budgeted for. It is enforced at TEST time, by
    # `prompts.worst_case_prompt_chars`, so raising any one cap past what the model can
    # hold fails the suite instead of failing a user's request. ~340k chars is ~85k
    # tokens on patent prose, comfortably inside this model's window.
    max_prompt_chars: int = 340_000

    max_selection_chars: int = 8_000
    # The uploaded file's NAME, not its text. It reaches a prompt, so it is capped like
    # every other untrusted string that does.
    max_context_name_chars: int = 120
    max_operations: int = 20
    # Per LLM call. 1.79x the slowest of 14 calls measured live (max 6.7 s).
    ai_node_timeout_seconds: float = 12.0
    # Extra draft attempts. `graph.max_draft_attempts()` derives from this at call time,
    # so the two cannot drift.
    judge_max_retries: int = 1
    # Wall-clock budget for the whole graph, checked at the top of every node. A backstop
    # for a hung socket: five node calls at 12 s is 60 s, already under this.
    ai_graph_deadline_seconds: float = 65.0
    # asyncio.wait_for around the whole run. Strictly above the graph deadline.
    ai_request_timeout_seconds: float = 75.0
    proposal_ttl_seconds: int = 900

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated_origins(cls, value: object) -> object:
        """Comma-separated is the documented form; a real list falls through."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def ai_enabled(self) -> bool:
        """False when the key is absent or still the `.env.example` placeholder, so a
        reviewer who forgets to paste a real key gets a clean 503 rather than a 500."""
        # .get_secret_value() is required: reading the field directly compares
        # "**********" against the placeholder prefix.
        key = (self.openai_api_key.get_secret_value() if self.openai_api_key else "").strip()
        return bool(key) and not key.startswith("sk-XXXX")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Not a module-level singleton, because tests need to vary the
    configuration and an import-time instance cannot be varied."""
    return Settings()
