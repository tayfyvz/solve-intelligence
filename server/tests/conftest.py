from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.ai.graph import GraphInput, GraphResult, get_ai_runner
from app.config import get_settings
from app.db import Base, create_db_engine
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client backed by an in-memory database.

    The session factory is injected into the app rather than overridden per request: the
    lifespan (which seeds) runs outside the request cycle, so `dependency_overrides`
    alone would let the tests write to server/data/app.db.
    """
    engine = create_db_engine("sqlite://")  # in-memory, StaticPool, same pragmas
    Base.metadata.create_all(engine)
    app = create_app(sessionmaker(bind=engine, autoflush=False))
    with TestClient(app) as test_client:  # runs the lifespan, which seeds
        yield test_client
    engine.dispose()


@pytest.fixture
def ai_settings(monkeypatch: pytest.MonkeyPatch):
    """Vary AI settings for one test, then put the cache back.

    `get_settings` is lru_cached, so setting an environment variable does nothing until
    the cache is cleared — and leaving a varied value cached would leak into every test
    that ran afterwards. The teardown clear matters more than the setup one.
    """
    get_settings.cache_clear()

    def _apply(**values: object):
        for name, value in values.items():
            monkeypatch.setenv(name.upper(), str(value))
        get_settings.cache_clear()
        return get_settings()

    yield _apply
    get_settings.cache_clear()


@pytest.fixture
def fake_runner(client: TestClient):
    """Install a canned `GraphResult` — or an exception to raise — as the graph.

    Substituted through `dependency_overrides`, so the real HTTP stack is exercised while
    nothing reaches OpenAI. `calls` records every `GraphInput` the route built, which is
    how the clamp, the history truncation and the filename are asserted from outside.
    """
    calls: list[GraphInput] = []

    def _install(result: GraphResult | None = None, raises: Exception | None = None):
        def runner(graph_input: GraphInput) -> GraphResult:
            calls.append(graph_input)
            if raises is not None:
                raise raises
            assert result is not None, "fake_runner needs a result or an exception"
            return result

        client.app.dependency_overrides[get_ai_runner] = lambda: runner
        return calls

    _install.calls = calls  # type: ignore[attr-defined]
    yield _install
    client.app.dependency_overrides.clear()
