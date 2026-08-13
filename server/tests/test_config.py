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
