"""V12 — two browser tabs pressing "Save as new version" at the same moment.

`create_version` reads MAX(version_number)+1 and then inserts, so concurrent
requests can compute the same number. The unique constraint stops the duplicate;
this pins that the *user* sees a 201 or a readable 409, never a 500.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, create_db_engine
from app.main import create_app

THREADS = 6
VERSIONS = "/api/documents/1/versions"


@pytest.fixture
def file_client(tmp_path: Path) -> Iterator[TestClient]:
    """A file-backed client, deliberately not the shared `client` fixture.

    `sqlite://` + StaticPool hands every thread the *same* DBAPI connection, so a
    threaded test against it dies with ResourceClosedError before reaching an
    assertion — it never exercises the race it claims to test.
    """
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    Base.metadata.create_all(engine)
    app = create_app(sessionmaker(bind=engine, autoflush=False))
    with TestClient(app) as test_client:  # runs the lifespan → seeds
        yield test_client
    engine.dispose()


def test_concurrent_create_version_never_500s(file_client: TestClient) -> None:
    def save(index: int) -> int:
        response = file_client.post(VERSIONS, json={"content": f"<p>{index}</p>"})
        return response.status_code

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        codes = list(pool.map(save, range(THREADS)))

    assert 500 not in codes
    assert set(codes) <= {201, 409}, codes

    # Whatever succeeded, the stored numbers must be 1..n: no duplicates, no gaps.
    listed = file_client.get(f"{VERSIONS}?limit=100").json()["items"]
    stored = sorted(v["version_number"] for v in listed)
    assert stored == list(range(1, len(stored) + 1))
    assert len(stored) == 1 + codes.count(201)
    # And every auto-generated name is distinct: a name collision must never be
    # what makes a concurrent save fail.
    assert len({v["name"] for v in listed}) == len(listed)
