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
    # NoDecode: pydantic-settings parses a list field as JSON *in the env source*,
    # before any validator runs, so `CORS_ORIGINS=http://a,http://b` — the form
    # everyone writes — would raise a JSONDecodeError at import, before there is
    # an app to report it. NoDecode hands the raw string to the validator below.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # SecretStr, not str: any repr() of this object — a log line, a ValidationError that
    # quotes its input, a debugger frame in a traceback — would otherwise print the live
    # key in full. SecretStr renders ********** everywhere and hands over the real value
    # only to an explicit .get_secret_value(). There are exactly two such call sites.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.2-2025-12-11"
    # Used ONLY by scripts/smoke_llm.py. The per-call budget the app uses is
    # `ai_node_timeout_seconds` below; wiring the wrong one is a silent 5x error.
    openai_timeout_seconds: float = 60.0

    max_content_bytes: int = 1_000_000  # save path
    max_html_chars: int = 200_000  # AI input
    max_instruction_chars: int = 2_000
    max_context_chars: int = 40_000
    # The Q&A branch's context budget. Large enough that a whole 37-page patent
    # (~112,000 chars) fits in ONE call, so on every realistic document the model reads
    # the document rather than a selection from it. MEASURED, not guessed: at a 106,827
    # char context the `answer` call ran in a median 2.3 s (n=6, min 1.6, max 3.5) —
    # faster than the same questions at 30,000, where the fragmented context made the
    # model work harder and one call hit `ai_node_timeout_seconds` outright.
    # It is NOT `max_html_chars` (200,000): retrieval must stay exercised on the
    # documents above this, because that range is reachable by design.
    # `claims_excerpt`'s own 30,000 default is a DIFFERENT budget on a different branch
    # (up to 5 calls, not 2) and must not be moved with this one.
    max_answer_context_chars: int = 120_000
    max_history_turns: int = 3

    # --- AI, Task 2 ------------------------------------------------------
    max_selection_chars: int = 8_000
    # The uploaded file's NAME, not its text. It is interpolated into the prompt,
    # so it is untrusted prompt input and is capped like every other string that
    # gets there. 120 is longer than any real filename and short enough that it
    # cannot carry instructions.
    max_context_name_chars: int = 120
    max_operations: int = 20
    # PER LLM CALL. Passed as `timeout=` on every chat.completions.parse. 1.79x the
    # slowest of the 14 calls 4Z measured (max 6.7 s, median 1.5 s — PLAN §20.7).
    ai_node_timeout_seconds: float = 12.0
    # "Extra draft attempts". graph.max_draft_attempts() is derived as this + 1, AT CALL
    # TIME, so the two can never drift and so this value is a real runtime lever
    # (PLAN §30.1). Do not add a third name for this number, and do not cache it in a
    # module constant.
    judge_max_retries: int = 1
    # Wall-clock budget for the whole graph, checked at the TOP of every node. It is a
    # HUNG-SOCKET BACKSTOP with no reachable path in the legitimate configuration — the
    # full derivation, and why that is correct rather than a gap, is in PLAN §3.4.
    ai_graph_deadline_seconds: float = 65.0
    # asyncio.wait_for around the whole run. Strictly above the graph deadline. Note
    # that it releases the REQUEST, not the worker thread (PLAN §3.4 point 3, §28.3).
    ai_request_timeout_seconds: float = 75.0
    proposal_ttl_seconds: int = 900
    # None => the kwarg is omitted entirely, and None is the SHIPPED value.
    #
    # 4Z measured `reasoning_effort="low"` accepted, and separately measured
    # `temperature` 0.0/1.0/2.0 accepted. Both measurements are correct. The
    # COMBINATION is not: gpt-5.2-2025-12-11 rejects `reasoning_effort` together with
    # any `temperature` other than the default 1, with
    #   400 "Unsupported value: 'temperature' does not support 0.0 with this model."
    # Measured 2026-08-14 (PLAN §27.4 correction 40):
    #   effort + no temperature -> OK      effort + temperature=0.0 -> 400
    #   effort + temperature=1.0 -> OK     no effort + temperature=0.0 -> OK
    # §21.2 sends temperature=0 on understand/plan_ops/judge, so with effort="low"
    # three of the five nodes 400 on every call and the feature does not work at all.
    #
    # `reasoning_effort` is the one that goes, because 4Z also measured
    # `reasoning_tokens == 0` on all 14 calls — it buys nothing measurable on this
    # model — whereas temperature=0 is what makes the same instruction resolve the
    # same way twice, which is the whole of §21.2's deterministic/generative split.
    # Setting this to a value again will 400 unless §21.2's temperatures go too.
    openai_reasoning_effort: str | None = None

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
        # .get_secret_value() is the ONLY place the raw key is read on this path.
        # Reading self.openai_api_key directly would compare "**********" against
        # the placeholder prefix and report every key as configured.
        key = (self.openai_api_key.get_secret_value() if self.openai_api_key else "").strip()
        return bool(key) and not key.startswith("sk-XXXX")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Deliberately not a module-level singleton: tests need to
    vary the configuration, and an import-time instance cannot be varied."""
    return Settings()
