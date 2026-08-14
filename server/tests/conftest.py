from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import Base, create_db_engine
from app.main import create_app


@pytest.fixture
def ai_settings(monkeypatch: pytest.MonkeyPatch):
    """Vary AI settings for one test, then put the cache back.

    `get_settings` is `lru_cache`d, so setting an environment variable does nothing until
    the cache is cleared — and leaving a varied value cached would leak into every test
    that ran afterwards. Both clears matter; the teardown one more than the setup one.
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
def client() -> Iterator[TestClient]:
    """A client backed by an in-memory database.

    The session factory is injected into the app rather than overridden per
    request: the lifespan (which seeds) runs outside the request cycle, so
    dependency_overrides alone would let the tests write to server/data/app.db.
    """
    engine = create_db_engine("sqlite://")  # in-memory, StaticPool, same pragmas
    Base.metadata.create_all(engine)
    app = create_app(sessionmaker(bind=engine, autoflush=False))
    with TestClient(app) as test_client:  # runs the lifespan → seeds
        yield test_client
    engine.dispose()
