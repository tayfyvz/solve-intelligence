"""V16 — paging both list endpoints.

The point of the ordering rules is that a client walking pages sees every row
exactly once. That is what these assert, rather than the shape of one response.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

DOCUMENTS = "/api/documents"
SEEDED = 2
EXTRA = 7  # 9 patents in total: two full pages of four and a partial third


@pytest.fixture
def many(client: TestClient) -> TestClient:
    for index in range(EXTRA):
        # Zero-padded so lexicographic order is numeric order, which keeps the
        # expected page contents obvious.
        assert client.post(DOCUMENTS, json={"title": f"Patent {index:02d}"}).status_code == 201
    return client


def walk(client: TestClient, path: str, key: str, limit: int) -> list[object]:
    """Every item the client sees walking the pages, in order."""
    seen: list[object] = []
    offset = 0
    while True:
        page = client.get(f"{path}?limit={limit}&offset={offset}").json()
        seen.extend(item[key] for item in page["items"])
        offset += limit
        if offset >= page["total"]:
            return seen


def test_document_pages_cover_every_row_exactly_once(many: TestClient) -> None:
    total = SEEDED + EXTRA
    everything = many.get(f"{DOCUMENTS}?limit=100").json()
    assert everything["total"] == total
    titles = [d["title"] for d in everything["items"]]
    assert titles == sorted(titles)

    first = many.get(f"{DOCUMENTS}?limit=4&offset=0").json()
    middle = many.get(f"{DOCUMENTS}?limit=4&offset=4").json()
    last = many.get(f"{DOCUMENTS}?limit=4&offset=8").json()

    assert [len(p["items"]) for p in (first, middle, last)] == [4, 4, 1]
    assert all(p["total"] == total for p in (first, middle, last))
    assert first["limit"] == 4 and middle["offset"] == 4

    # The envelope echoes the request, and the walk reconstructs the whole list
    # with no duplicates and no gaps — at three different page sizes.
    for limit in (1, 4, 100):
        assert walk(many, DOCUMENTS, "title", limit) == titles


def test_version_pages_are_newest_first_and_cover_every_row(many: TestClient) -> None:
    for index in range(4):
        created = many.post(f"{DOCUMENTS}/1/versions", json={"content": f"<p>{index}</p>"})
        assert created.status_code == 201

    page = many.get(f"{DOCUMENTS}/1/versions?limit=2&offset=0").json()
    assert page["total"] == 5
    assert [v["version_number"] for v in page["items"]] == [5, 4]

    assert walk(many, f"{DOCUMENTS}/1/versions", "version_number", 2) == [5, 4, 3, 2, 1]


@pytest.mark.parametrize("path", [DOCUMENTS, f"{DOCUMENTS}/1/versions"])
def test_an_offset_past_the_end_is_an_empty_page_not_a_404(client: TestClient, path: str) -> None:
    response = client.get(f"{path}?offset=5000")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] > 0  # the client can still see where the data ends


@pytest.mark.parametrize("path", [DOCUMENTS, f"{DOCUMENTS}/1/versions"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
def test_out_of_range_paging_parameters_are_422(client: TestClient, path: str, query: str) -> None:
    """The bounds are FastAPI's, so an unreadable limit never reaches SQL."""
    assert client.get(f"{path}?{query}").status_code == 422


@pytest.mark.parametrize("path", [DOCUMENTS, f"{DOCUMENTS}/1/versions"])
def test_an_offset_past_the_64_bit_range_is_422_not_500(client: TestClient, path: str) -> None:
    """`ge=0` alone let an integer wider than SQLite's INTEGER through to the
    driver, which raised OverflowError — a 500 for a bad query string. The path
    ids were already bounded for exactly this; the offset reaches the same driver.
    """
    assert client.get(f"{path}?offset=9223372036854775808").status_code == 422
    assert client.get(f"{path}?offset=9223372036854775807").status_code == 200  # the bound itself


@pytest.mark.parametrize("path", [DOCUMENTS, f"{DOCUMENTS}/1/versions"])
def test_the_limit_bounds_themselves_are_accepted(client: TestClient, path: str) -> None:
    assert len(client.get(f"{path}?limit=1").json()["items"]) == 1
    assert client.get(f"{path}?limit=100").status_code == 200


def test_document_reads_never_select_version_content(many: TestClient) -> None:
    """The list is titles and counts; loading every draft body to render it is the
    performance bug this guards. Asserted on the emitted SQL, because a passing
    response body cannot tell you what was fetched to build it."""
    statements: list[str] = []

    def record(_conn, _cursor, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = many.app.state.session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert many.get(f"{DOCUMENTS}?limit=100").status_code == 200
        assert many.get(f"{DOCUMENTS}/1").status_code == 200
        assert many.get(f"{DOCUMENTS}/1/versions").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements, "No SQL was captured — the guard would pass on nothing."
    assert not any("content" in statement for statement in statements), statements
