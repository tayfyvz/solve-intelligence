"""The REST surface: versioning, names, paging, and the HTTP layer below the routes.

The versioning rules are challenge task 1; the rest are the cases no route handler ever
sees, found by pointing something other than the app's own client at the server.
"""

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.crud import seed_if_empty
from app.db import Base, create_db_engine
from app.main import BODY_TOO_DEEP, BODY_TOO_LARGE, MAX_JSON_DEPTH, create_app
from app.models import Document, DocumentVersion

DOCUMENTS = "/api/documents"
VERSIONS = f"{DOCUMENTS}/1/versions"
SEED_TITLE = "Wireless optogenetic device for remotely controlling neural activitiies"
JSON = {"Content-Type": "application/json"}
ORIGIN = "http://localhost:5173"


def version_count(client: TestClient, document_id: int = 1) -> int:
    return client.get(f"{DOCUMENTS}/{document_id}").json()["version_count"]


# ------------------------------------------------------------------------- versioning


def test_put_updates_in_place_and_never_creates_a_version(client: TestClient) -> None:
    """Challenge task 1.3, and the most important assertion in the suite.

    Saving unchanged content is still a save: without an explicit touch the attribute
    stays clean, SQLAlchemy emits no UPDATE, `onupdate` never fires and "last saved" in
    the version dropdown silently goes stale. `updated_at` is backdated rather than slept
    through, because SQLite's CURRENT_TIMESTAMP has one-second resolution.

    The lookup filters on document_id as well as the number: both seed patents have a
    version 1, so a lookup keyed on the number alone would overwrite the wrong patent.
    """
    original = client.get(f"{VERSIONS}/1").json()["content"]
    other = client.get(f"{DOCUMENTS}/2/versions/1").json()["content"]

    edited = client.put(f"{VERSIONS}/1", json={"content": "<p>edited</p><script>x</script>"})
    assert edited.status_code == 200
    assert edited.json()["version_number"] == 1
    assert client.get(f"{VERSIONS}/1").json()["content"] == "<p>edited</p>"  # and sanitised
    assert original != "<p>edited</p>"
    assert version_count(client) == 1
    assert client.get(f"{DOCUMENTS}/2/versions/1").json()["content"] == other

    stale = datetime(2020, 1, 1)
    with client.app.state.session_factory() as db:
        db.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == 1, DocumentVersion.version_number == 1)
            .values(updated_at=stale)
        )
        db.commit()
    unchanged = client.put(f"{VERSIONS}/1", json={"content": "<p>edited</p>"})
    assert datetime.fromisoformat(unchanged.json()["updated_at"]) > stale
    assert version_count(client) == 1

    # A patent's "last touched" is its newest version's save time, computed in SQL.
    assert datetime.fromisoformat(client.get(f"{DOCUMENTS}/1").json()["updated_at"]) > stale

    # PUT never renames either: the name is not in the body, and an extra field is
    # ignored rather than applied.
    client.patch(f"{VERSIONS}/1", json={"name": "Named"})
    renamed = client.put(f"{VERSIONS}/1", json={"content": "<p>x</p>", "name": "Other"})
    assert renamed.json()["name"] == "Named"
    assert version_count(client) == 1


def test_post_creates_the_next_version_and_get_returns_the_one_asked_for(
    client: TestClient,
) -> None:
    original = client.get(f"{VERSIONS}/1").json()["content"]

    created = client.post(VERSIONS, json={"content": "<p>second draft</p>"})
    assert created.status_code == 201 and created.json()["version_number"] == 2
    assert client.post(VERSIONS, json={"content": "<p>v3</p>"}).json()["version_number"] == 3
    assert version_count(client) == 3

    assert client.get(f"{VERSIONS}/1").json()["content"] == original
    assert client.get(f"{VERSIONS}/2").json()["content"] == "<p>second draft</p>"


def test_delete_removes_one_version_and_never_the_last_one(client: TestClient) -> None:
    """A document can never be left with zero versions — there would be nothing to open.
    Survivors keep their own numbers: a version number is a stable identifier, not a
    positional index, so nothing renumbers or reorders."""
    client.post(VERSIONS, json={"content": "<p>v2</p>"})
    client.post(VERSIONS, json={"content": "<p>v3</p>"})
    v3_before = client.get(f"{VERSIONS}/3").json()["content"]

    removed = client.delete(f"{VERSIONS}/2")
    assert removed.status_code == 204 and removed.content == b""
    assert client.get(f"{VERSIONS}/2").status_code == 404
    assert client.get(f"{VERSIONS}/3").json()["content"] == v3_before
    assert version_count(client) == 2

    client.delete(f"{VERSIONS}/3")
    refused = client.delete(f"{VERSIONS}/1")
    assert refused.status_code == 409
    assert client.get(f"{VERSIONS}/1").status_code == 200

    # And a delete on one document cannot reach another, same as the PUT path.
    assert client.get(f"{DOCUMENTS}/2").json()["version_count"] == 1


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("GET", f"{DOCUMENTS}/999", "Document 999 not found."),
        ("GET", f"{DOCUMENTS}/999/versions", "Document 999 not found."),
        ("GET", f"{VERSIONS}/99", "Version 99 of document 1 not found."),
        ("POST", f"{DOCUMENTS}/999/versions", "Document 999 not found."),
        ("PUT", f"{DOCUMENTS}/999/versions/1", "Document 999 not found."),
        ("PUT", f"{VERSIONS}/99", "Version 99 of document 1 not found."),
        ("DELETE", f"{VERSIONS}/99", "Version 99 of document 1 not found."),
        ("PATCH", f"{DOCUMENTS}/999/versions/1", "Document 999 not found."),
    ],
)
def test_a_missing_document_and_a_missing_version_say_different_things(
    client: TestClient, method: str, path: str, detail: str
) -> None:
    """The document is checked first, so /documents/999/versions/1 blames the document
    rather than the version."""
    response = client.request(method, path, json={"content": "<p>x</p>", "name": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", f"{VERSIONS}/1", {}),
        ("PUT", f"{VERSIONS}/1", {"content": None}),
        ("POST", VERSIONS, {"content": 5}),
        ("GET", f"{VERSIONS}/0", None),
        ("GET", f"{DOCUMENTS}/abc", None),
        # Larger than SQLite's 64-bit INTEGER: unbounded, this reaches the driver and
        # raises OverflowError as a 500.
        ("GET", f"{DOCUMENTS}/99999999999999999999999", None),
        ("PUT", f"{VERSIONS}/99999999999999999999999", {"content": "<p>x</p>"}),
    ],
)
def test_invalid_bodies_and_paths_are_422(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    assert client.request(method, path, json=body).status_code == 422


def test_content_is_sanitised_size_capped_in_bytes_and_may_be_empty(client: TestClient) -> None:
    """A user may legitimately clear a draft, so "" round-trips. The cap is UTF-8 BYTES
    because it protects the database and the wire, and a rejected write leaves the stored
    version exactly as it was."""
    dangerous = '<p onclick="steal()">keep</p><iframe src="evil"></iframe>'
    for write in (
        client.put(f"{VERSIONS}/1", json={"content": dangerous}),
        client.post(VERSIONS, json={"content": dangerous}),
    ):
        assert write.json()["content"] == "<p>keep</p>"

    assert client.put(f"{VERSIONS}/1", json={"content": ""}).status_code == 200
    assert client.get(f"{VERSIONS}/1").json()["content"] == ""

    cap = get_settings().max_content_bytes
    oversized = client.put(f"{VERSIONS}/1", json={"content": "a" * (cap + 1)})
    assert oversized.status_code == 413 and str(cap) in oversized.json()["detail"]
    assert client.get(f"{VERSIONS}/1").json()["content"] == ""

    # Under the character cap, over the byte cap.
    assert client.post(VERSIONS, json={"content": "é" * (cap // 2 + 1)}).status_code == 413


# ------------------------------------------------------------------------------ names


def test_creating_a_patent_makes_exactly_one_named_version(client: TestClient) -> None:
    """A patent with no versions has nothing to open, so both are created in one
    transaction — and a rejected create leaves nothing half-made."""
    created = client.post(DOCUMENTS, json={"title": "  Widget  ", "content": "<p>hi</p>"})
    assert created.status_code == 201
    detail = created.json()
    assert detail["title"] == "Widget"  # stored trimmed
    assert detail["version_count"] == 1 and detail["latest_version_number"] == 1

    first = client.get(f"{DOCUMENTS}/{detail['id']}/versions").json()["items"][0]
    assert first["version_number"] == 1 and first["name"] == "Version 1"
    assert client.get(f"{DOCUMENTS}/{detail['id']}/versions/1").json()["content"] == "<p>hi</p>"

    # Content is optional: a new patent starts blank.
    blank = client.post(DOCUMENTS, json={"title": "Blank"})
    assert client.get(f"{DOCUMENTS}/{blank.json()['id']}/versions/1").json()["content"] == ""

    cap = get_settings().max_content_bytes
    oversized = client.post(DOCUMENTS, json={"title": "Huge", "content": "a" * (cap + 1)})
    assert oversized.status_code == 413
    listed = client.get(f"{DOCUMENTS}?limit=100").json()["items"]
    assert not any(d["title"] == "Huge" for d in listed)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (SEED_TITLE, 409),
        (f"   {SEED_TITLE}   ", 409),  # trimmed before comparison
        (SEED_TITLE.upper(), 409),  # compared case-insensitively
        (f"<b>{SEED_TITLE}</b>", 409),  # …and on the sanitised form
        ("", 422),
        ("   ", 422),  # whitespace is stripped before the length check
        ("x" * 201, 422),
        ("<script>alert(1)</script>", 422),  # no plain text left after sanitising
        ("x" * 200, 201),
    ],
)
def test_titles_are_trimmed_sanitised_and_unique(
    client: TestClient, title: str, expected: int
) -> None:
    assert client.post(DOCUMENTS, json={"title": title}).status_code == expected


def test_renaming_stores_plain_text_and_a_patent_may_keep_its_own_title(
    client: TestClient,
) -> None:
    """`content` is the only field that passes a sanitiser otherwise, so a title and a
    version name are the one string on this server that would reach a browser exactly as
    typed — and they are also quoted back inside 409 messages.

    The uniqueness pre-check excludes the row itself, so a no-op save — or a pure
    re-casing — is not a conflict with itself. Renaming touches no version.
    """
    scripted = client.post(DOCUMENTS, json={"title": "<script>alert(1)</script>Widget"})
    assert scripted.json()["title"] == "Widget"
    renamed = client.patch(f"{DOCUMENTS}/1", json={"title": "<b>Bold</b>\r\ntitle"})
    assert renamed.json()["title"] == "Bold title"
    assert client.get(f"{VERSIONS}/1").json()["content"].startswith("<h1>Claims</h1>")

    named = client.post(VERSIONS, json={"content": "", "name": "<i>Draft</i>"})
    assert named.json()["name"] == "Draft"
    assert client.patch(f"{VERSIONS}/1", json={"name": "<i>Filed</i>"}).json()["name"] == "Filed"

    assert client.patch(f"{DOCUMENTS}/1", json={"title": "Bold title"}).status_code == 200
    assert client.patch(f"{DOCUMENTS}/1", json={"title": "BOLD TITLE"}).status_code == 200
    taken = {"title": "Microfluidic Device for Blood Oxygenation"}
    assert client.patch(f"{DOCUMENTS}/1", json=taken).status_code == 409


def test_version_names_are_unique_per_patent_and_auto_names_never_collide(
    client: TestClient,
) -> None:
    """An omitted name must never be able to fail the request, so it steps around names a
    user has already taken. Uniqueness is per patent, so the same name in another patent
    is fine."""
    assert client.post(VERSIONS, json={"content": "", "name": "Draft"}).status_code == 201
    conflict = client.patch(f"{VERSIONS}/1", json={"name": "draft"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == 'Version "draft" already exists in this patent.'
    assert client.patch(f"{DOCUMENTS}/2/versions/1", json={"name": "Draft"}).status_code == 200
    assert client.patch(f"{VERSIONS}/2", json={"name": "Draft"}).status_code == 200  # its own name

    client.patch(f"{VERSIONS}/1", json={"name": "Version 3"})
    client.patch(f"{VERSIONS}/2", json={"name": "version 3 (2)"})
    auto = client.post(VERSIONS, json={"content": ""})
    assert auto.status_code == 201
    assert auto.json()["version_number"] == 3 and auto.json()["name"] == "Version 3 (3)"

    duplicate = client.post(VERSIONS, json={"content": "", "name": "version 3"})
    assert duplicate.status_code == 409
    assert version_count(client) == 3  # nothing was created


@pytest.mark.parametrize(
    "build_row",
    [
        lambda: Document(title=SEED_TITLE.upper()),
        lambda: DocumentVersion(document_id=1, version_number=2, name="VERSION 1", content=""),
    ],
    ids=["patent title", "version name"],
)
def test_the_unique_indexes_catch_what_a_pre_check_never_saw(client: TestClient, build_row) -> None:
    """The pre-checks produce the readable 409; these indexes are what survives two
    writers racing past them. Written through the session, because that is the only way
    to skip the pre-check."""
    with client.app.state.session_factory() as db, pytest.raises(IntegrityError):
        db.add(build_row())
        db.commit()


# ---------------------------------------------------------------------------- the seed


def test_the_seed_is_stored_in_getHTML_shape_and_seeding_is_idempotent(
    client: TestClient,
) -> None:
    """The seed source is pretty-printed full HTML; production content is collapsed
    single-line body HTML. Storing it pre-normalised is what stops the parser seeing two
    shapes — and the idempotence guard is a count, not hardcoded ids, or a second boot
    against a file-backed database raises IntegrityError and the app never starts.
    """
    assert client.get(DOCUMENTS).json()["total"] == 2

    content = client.get(f"{VERSIONS}/1").json()["content"]
    for absent in ("\n", "<!DOCTYPE", "<title>", "<body>", "> <"):
        assert absent not in content
    assert content.startswith("<h1>Claims</h1>")
    assert content.count("<p>") == 19

    # The title survived the move out of <title>, inherited typo included, and two pieces
    # of deliberate test material survived normalisation: patent 1's real cross-reference
    # error, and a plain ASCII apostrophe TipTap does not entity-encode.
    assert client.get(f"{DOCUMENTS}/1").json()["title"] == SEED_TITLE
    assert "7. The method of claim 5," in content
    assert "the system's versatility" in content

    with client.app.state.session_factory() as db:
        assert seed_if_empty(db) == 0
    assert client.get(DOCUMENTS).json()["total"] == 2


# --------------------------------------------------------------------------- paging


def test_paging_covers_every_row_exactly_once(client: TestClient) -> None:
    """The point of the ordering rules is that a client walking pages sees every row
    once. Titles are unique, but the id tiebreak makes the order total by construction,
    so a page can never repeat or skip."""
    for index in range(7):  # zero-padded, so lexicographic order is numeric order
        assert client.post(DOCUMENTS, json={"title": f"Patent {index:02d}"}).status_code == 201

    def walk(path: str, key: str, limit: int) -> list[object]:
        seen, offset = [], 0
        while True:
            page = client.get(f"{path}?limit={limit}&offset={offset}").json()
            seen.extend(item[key] for item in page["items"])
            offset += limit
            if offset >= page["total"]:
                return seen

    everything = client.get(f"{DOCUMENTS}?limit=100").json()
    titles = [d["title"] for d in everything["items"]]
    assert everything["total"] == 9 and titles == sorted(titles)
    for limit in (1, 4, 100):
        assert walk(DOCUMENTS, "title", limit) == titles

    for index in range(4):
        client.post(VERSIONS, json={"content": f"<p>{index}</p>"})
    page = client.get(f"{VERSIONS}?limit=2").json()
    assert page["total"] == 5
    assert [v["version_number"] for v in page["items"]] == [5, 4]  # newest first
    assert walk(VERSIONS, "version_number", 2) == [5, 4, 3, 2, 1]

    # An offset past the end is an empty page, not a 404: it is a stale link, and
    # `total` still tells the client where the data ends. Out-of-range values are 422s,
    # including one wider than SQLite's INTEGER — unbounded, that reaches the driver and
    # raises OverflowError as a 500.
    for path in (DOCUMENTS, VERSIONS):
        past_the_end = client.get(f"{path}?offset=5000")
        assert past_the_end.status_code == 200
        assert past_the_end.json()["items"] == [] and past_the_end.json()["total"] > 0

        for bad in ("limit=0", "limit=101", "offset=-1", "limit=abc", "offset=9223372036854775808"):
            assert client.get(f"{path}?{bad}").status_code == 422, bad
        assert client.get(f"{path}?offset=9223372036854775807").status_code == 200
        assert len(client.get(f"{path}?limit=1").json()["items"]) == 1


def test_document_reads_never_select_version_content(client: TestClient) -> None:
    """The list is titles and counts; loading every draft body to render it is the
    performance bug this guards. Asserted on the emitted SQL, because a passing response
    body cannot tell you what was fetched to build it."""
    statements: list[str] = []

    def record(_conn, _cursor, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = client.app.state.session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(f"{DOCUMENTS}?limit=100").status_code == 200
        assert client.get(f"{DOCUMENTS}/1").status_code == 200
        assert client.get(VERSIONS).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements, "no SQL was captured — the guard would pass on nothing"
    assert not any("content" in statement for statement in statements), statements


# ------------------------------------------------------------------- the HTTP surface


def test_head_is_served_wherever_get_is(client: TestClient) -> None:
    """RFC 9110 requires it and health checkers use it; every GET route answered 405. The
    headers must match the GET's — an empty body with a Content-Length of 0 would tell a
    caller the resource is empty. The method is rewritten, not the routing, so a HEAD
    still gets the real status."""
    for path in ("/api/health", DOCUMENTS, f"{DOCUMENTS}/1", VERSIONS):
        head, get = client.head(path), client.get(path)
        assert head.status_code == get.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == get.headers["content-length"]
        assert head.headers["content-type"] == get.headers["content-type"]

    assert client.head(f"{DOCUMENTS}/999").status_code == 404


def test_hostile_or_oversized_bodies_are_refused_before_a_parser_sees_them(
    client: TestClient,
) -> None:
    """`json.loads` recurses, so ~1000 nested brackets — a few kilobytes — exhausted the
    stack and every JSON route answered 500. The scanner skips string contents, or a
    patent whose text contains braces would be refused for a structure it does not have.

    The size guard is a multiple of the content cap, so the router's own 413 — the one
    carrying a byte count the user can act on — still runs.
    """
    deep = client.post(DOCUMENTS, content="[" * 3000 + "]" * 3000, headers=JSON)
    assert deep.status_code == 400 and deep.json()["detail"] == BODY_TOO_DEEP

    at_limit = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH
    assert client.post(DOCUMENTS, content=at_limit, headers=JSON).status_code == 422

    braces = "{" * (MAX_JSON_DEPTH * 2) + ' \\" [ ['
    assert client.post(DOCUMENTS, json={"title": braces, "content": ""}).status_code == 201

    cap = get_settings().max_content_bytes
    huge = client.put(f"{VERSIONS}/1", content=b"x" * (cap * 2 + 1), headers=JSON)
    assert huge.status_code == 413 and huge.json()["detail"] == BODY_TOO_LARGE

    # The logging policy applies to responses too. FastAPI's default 422 handler puts the
    # rejected value in an `input` field, so a 10,000-character title — a customer's
    # unpublished patent text on this server — went back over the wire in full.
    secret = "ZZQQ-TITLE-SECRET"
    response = client.post(DOCUMENTS, json={"title": secret * 600})

    assert response.status_code == 422
    assert secret not in response.text
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "title"] and detail[0]["msg"]
    assert "input" not in detail[0]


def test_an_unhandled_error_reaches_the_browser_as_a_readable_500(
    client: TestClient, caplog
) -> None:
    """Without the handler, FastAPI's built-in 500 is produced ABOVE CORSMiddleware and
    carries no Access-Control-Allow-Origin header — so the browser reports a CORS failure,
    the fetch rejects at the network level, and the UI says "cannot reach the server"
    while the server is up.

    `logger.exception` would render the traceback, whose last line is `str(exc)`; a
    ValidationError's message quotes the input that failed, and here that is a customer's
    patent. The TYPE and the path are logged, and nothing that has been near a document.
    """
    secret = "A claim-1 fragment that must never be logged"

    @client.app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        response = client.get("/api/boom", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong on the server."}
    assert response.headers["access-control-allow-origin"] == ORIGIN

    logged = "\n".join(
        [r.getMessage() for r in caplog.records] + [r.exc_text or "" for r in caplog.records]
    )
    assert secret not in logged
    assert "RuntimeError" in logged and "/api/boom" in logged

    # The guard must not have displaced CORS from the happy path.
    ok = client.get(DOCUMENTS, headers={"Origin": ORIGIN})
    assert ok.headers["access-control-allow-origin"] == ORIGIN

    # And the negotiated policy, which a preflight is the only place to observe. A
    # hand-written method list drifted in both directions at once: it allowed a verb no
    # route served and omitted the PATCH both rename features use, so every rename failed
    # preflight and the UI reported "cannot reach the server".
    #
    # `allow_credentials` is the flag that tells a browser to attach ambient credentials
    # cross-origin. There is no auth, no cookie and no Authorization header anywhere in
    # the client, so it buys nothing and only widens the surface.
    def preflight(path: str, method: str):
        return client.options(
            path,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    response = preflight(DOCUMENTS, "POST")
    assert response.status_code == 200
    assert "access-control-allow-credentials" not in response.headers
    assert "*" not in response.headers["access-control-allow-headers"]

    served = {
        method
        for route in client.app.routes
        for method in getattr(route, "methods", ())
        if method not in {"OPTIONS", "HEAD"}
    }
    allowed = {m.strip() for m in response.headers["access-control-allow-methods"].split(",")}
    assert allowed == served | {"OPTIONS", "HEAD"}

    # The exact request a browser sends before a rename, which used to come back 400.
    for path in (f"{DOCUMENTS}/1", f"{VERSIONS}/1"):
        assert "PATCH" in preflight(path, "PATCH").headers["access-control-allow-methods"]


# ------------------------------------------------------------ configuration and races


def test_configuration_never_crashes_the_process_before_the_app_exists() -> None:
    """A list field is JSON-parsed by pydantic-settings, so `a,b` — the form everyone
    writes — used to raise at import: a traceback with no server to report it.

    The key is a SecretStr, so no repr of these settings can print it, and the shipped
    `.env.example` placeholder still reads as unconfigured — a reviewer who runs
    `cp .env.example .env` must get a clean 503, not an authentication 500.
    """
    assert Settings(cors_origins="http://a.test, http://b.test").cors_origins == [
        "http://a.test",
        "http://b.test",
    ]
    assert Settings().cors_origins == ["http://localhost:5173"]

    settings = Settings(openai_api_key="sk-live-secret-value")
    assert "sk-live-secret-value" not in repr(settings) + str(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-live-secret-value"
    assert settings.ai_enabled is True

    for unusable in ("sk-XXXXXXXX", None, "   "):
        assert Settings(openai_api_key=unusable).ai_enabled is False


def test_a_non_sqlite_url_gets_no_sqlite_only_connect_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check_same_thread` is a sqlite3-only connect argument and psycopg raises TypeError
    on it, so passing it unconditionally made "port to Postgres by changing the URL"
    false. The kwargs our code computes are captured at the seam rather than asserted on
    a built engine, because `create_engine` imports the DBAPI eagerly and psycopg is not
    a dependency of this project."""
    from sqlalchemy import StaticPool

    from app import db

    seen: dict[str, object] = {}
    real_create_engine = db.create_engine  # bound BEFORE the patch, or the spy calls itself

    def spy(url: str, **kwargs: object):
        seen["kwargs"] = kwargs
        return real_create_engine("sqlite://")

    monkeypatch.setattr(db, "create_engine", spy)

    db.create_db_engine("postgresql+psycopg://u:p@h/d")
    assert seen["kwargs"] == {}, "no sqlite-only connect_args, and no StaticPool"

    db.create_db_engine("sqlite://")
    assert seen["kwargs"] == {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }


@pytest.fixture
def file_client(tmp_path: Path) -> Iterator[TestClient]:
    """A file-backed client, deliberately not the shared one: `sqlite://` with StaticPool
    hands every thread the SAME connection, so a threaded test against it dies with
    ResourceClosedError before reaching an assertion — it never exercises the race."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    Base.metadata.create_all(engine)
    with TestClient(create_app(sessionmaker(bind=engine, autoflush=False))) as test_client:
        yield test_client
    engine.dispose()


def test_concurrent_saves_never_500_and_never_duplicate_a_number(file_client: TestClient) -> None:
    """`create_version` reads MAX+1 and then inserts, so two tabs saving at the same
    moment compute the same number. The unique constraint stops the duplicate and the
    retry recomputes; what the user sees is a 201 or a readable 409, never a 500."""
    threads = 6

    def save(index: int) -> int:
        return file_client.post(VERSIONS, json={"content": f"<p>{index}</p>"}).status_code

    with ThreadPoolExecutor(max_workers=threads) as pool:
        codes = list(pool.map(save, range(threads)))

    assert set(codes) <= {201, 409}, codes

    listed = file_client.get(f"{VERSIONS}?limit=100").json()["items"]
    stored = sorted(v["version_number"] for v in listed)
    assert stored == list(range(1, len(stored) + 1))  # no duplicates, no gaps
    assert len(stored) == 1 + codes.count(201)
    # An auto-generated name collision must never be what makes a concurrent save fail.
    assert len({v["name"] for v in listed}) == len(listed)
