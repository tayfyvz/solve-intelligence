"""V14 — configuration must not crash the process before the app exists."""

from app.config import Settings


def test_cors_origins_accepts_the_comma_separated_form() -> None:
    """A list field is JSON-parsed by pydantic-settings, so `a,b` used to raise a
    JSONDecodeError at import — a traceback with no server to report it."""
    assert Settings(cors_origins="http://a.test, http://b.test").cors_origins == [
        "http://a.test",
        "http://b.test",
    ]
    assert Settings().cors_origins == ["http://localhost:5173"]


def test_the_api_key_never_appears_in_a_repr() -> None:
    """SecretStr, not str: any repr of this object — a log line, a ValidationError that
    quotes its input, a debugger frame — would otherwise print the live key in full."""
    settings = Settings(openai_api_key="sk-live-secret-value-0123456789")
    assert "sk-live-secret-value" not in repr(settings)
    assert "sk-live-secret-value" not in str(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-live-secret-value-0123456789"


def test_the_env_example_placeholder_still_reads_as_unconfigured() -> None:
    """The one thing the SecretStr retype could break silently: `ai_enabled` compares a
    string against a prefix, and that value is now wrapped. A reviewer who runs
    `cp .env.example .env` must get a clean 503, not an authentication 500."""
    assert Settings(openai_api_key="sk-XXXXXXXX").ai_enabled is False
    assert Settings(openai_api_key=None).ai_enabled is False
    assert Settings(openai_api_key="   ").ai_enabled is False
    assert Settings(openai_api_key="sk-real-looking-key").ai_enabled is True
