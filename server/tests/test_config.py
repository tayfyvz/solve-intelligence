"""V14 — configuration must not crash the process before the app exists."""

from sqlalchemy import StaticPool

from app import db
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


def test_a_non_sqlite_url_gets_no_sqlite_only_connect_args(monkeypatch) -> None:
    """The ship-blocker behind the portability claim.

    `check_same_thread` is a sqlite3-only connect argument and psycopg raises
    TypeError on it, so passing it unconditionally made "port to Postgres by
    changing the URL" false. StaticPool is scoped inside the same guard.

    This was first written as `create_db_engine("postgresql+psycopg://...")`
    asserting on the built engine, on the stated grounds that "engine construction
    is lazy; only .connect() dials out". That is only half true and the test does
    not run: create_engine imports the DBAPI module eagerly, and `psycopg` is not a
    dependency of this project — the call raises ModuleNotFoundError before it
    reaches any of our code. Adding a driver we never use, to a production image,
    to run one test, is the wrong trade. So the kwargs our code computes are
    captured at the seam instead, which is the claim itself: for a non-sqlite URL
    we pass no sqlite-only arguments.
    """
    seen: dict[str, object] = {}
    real_create_engine = db.create_engine  # bound BEFORE the patch, or _spy calls itself

    def _spy(url: str, **kwargs: object):
        seen["url"] = url
        seen["kwargs"] = kwargs
        # Hand back a real, harmless engine so create_db_engine's own dialect-guarded
        # pragma listener still runs against something valid.
        return real_create_engine("sqlite://")

    monkeypatch.setattr(db, "create_engine", _spy)

    db.create_db_engine("postgresql+psycopg://u:p@h/d")
    assert seen["kwargs"] == {}, "no sqlite-only connect_args, and no StaticPool"

    db.create_db_engine("sqlite://")
    assert seen["kwargs"] == {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
