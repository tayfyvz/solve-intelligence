"""Task 1: versioning routes."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.config import get_settings
from app.models import DocumentVersion

DOC = 1
VERSIONS = f"/api/documents/{DOC}/versions"


def version_count(client: TestClient, document_id: int = DOC) -> int:
    return client.get(f"/api/documents/{document_id}").json()["version_count"]


def test_put_updates_in_place_and_creates_no_version(client: TestClient) -> None:
    """V1 — the most important assertion in the suite (challenge task 1.3)."""
    before = version_count(client)
    original = client.get(f"{VERSIONS}/1").json()["content"]

    response = client.put(f"{VERSIONS}/1", json={"content": "<p>edited</p><script>x</script>"})

    assert response.status_code == 200
    assert response.json()["version_number"] == 1
    assert version_count(client) == before
    stored = client.get(f"{VERSIONS}/1").json()["content"]
    assert stored == "<p>edited</p>"  # updated, and sanitised
    assert stored != original


def test_put_bumps_updated_at_even_when_content_is_unchanged(client: TestClient) -> None:
    """V1b — pressing Save without typing is still a save.

    Unchanged content leaves the attribute clean, so SQLAlchemy would emit no
    UPDATE, `onupdate` would never fire, and "last saved" in the dropdown would
    silently stay stale. `updated_at` is backdated rather than slept through:
    SQLite's CURRENT_TIMESTAMP has one-second resolution.
    """
    unchanged = client.get(f"{VERSIONS}/1").json()["content"]
    before = version_count(client)
    stale = datetime(2020, 1, 1)
    with client.app.state.session_factory() as db:
        db.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == DOC, DocumentVersion.version_number == 1)
            .values(updated_at=stale)
        )
        db.commit()
    assert client.get(f"{VERSIONS}/1").json()["updated_at"].startswith("2020-01-01")

    response = client.put(f"{VERSIONS}/1", json={"content": unchanged})

    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["updated_at"]) > stale
    assert client.get(f"{VERSIONS}/1").json()["content"] == unchanged
    assert version_count(client) == before  # invariant: PUT never creates a version


def test_put_on_one_document_cannot_touch_another(client: TestClient) -> None:
    """V1c — the version lookup filters on document_id as well as the number.

    Both seed patents have a version 1, so a lookup keyed on the number alone
    would happily overwrite the wrong patent.
    """
    other = client.get("/api/documents/2/versions/1").json()["content"]

    assert client.put(f"{VERSIONS}/1", json={"content": "<p>only doc 1</p>"}).status_code == 200

    assert client.get("/api/documents/2/versions/1").json()["content"] == other
    assert client.get(f"{VERSIONS}/1").json()["content"] == "<p>only doc 1</p>"


def test_post_creates_next_version_and_leaves_others_untouched(client: TestClient) -> None:
    """V2."""
    original = client.get(f"{VERSIONS}/1").json()["content"]

    response = client.post(VERSIONS, json={"content": "<p>v2</p>"})

    assert response.status_code == 201
    assert response.json()["version_number"] == 2
    assert client.get(f"{VERSIONS}/1").json()["content"] == original

    assert client.post(VERSIONS, json={"content": "<p>v3</p>"}).json()["version_number"] == 3
    assert version_count(client) == 3


def test_get_returns_the_requested_versions_content(client: TestClient) -> None:
    """V3 — reading an older version must not return the newest one."""
    client.post(VERSIONS, json={"content": "<p>second draft</p>"})

    v1 = client.get(f"{VERSIONS}/1").json()
    v2 = client.get(f"{VERSIONS}/2").json()

    assert v1["version_number"] == 1
    assert v2["content"] == "<p>second draft</p>"
    assert v1["content"] != v2["content"]


def test_list_and_detail_shapes(client: TestClient) -> None:
    """V4."""
    page = client.get("/api/documents").json()
    assert page["total"] == 2
    # Ordered by title, so the Microfluidic patent (id 2) sorts first.
    assert [d["id"] for d in page["items"]] == [2, 1]
    assert page["items"][1]["title"].startswith("Wireless optogenetic device")
    assert "content" not in page["items"][0]

    detail = client.get(f"/api/documents/{DOC}").json()
    assert detail["title"] == page["items"][1]["title"]
    assert detail["version_count"] == 1
    assert detail["latest_version_number"] == 1
    assert detail["updated_at"]

    versions = client.get(VERSIONS).json()
    assert [v["version_number"] for v in versions["items"]] == [1]
    assert versions["items"][0]["name"] == "Version 1"
    assert versions["items"][0]["updated_at"]
    # The dropdown must not carry document bodies.
    assert "content" not in versions["items"][0]


def test_document_updated_at_tracks_its_versions(client: TestClient) -> None:
    """V4b — "last touched" is the newest version's save time, computed in SQL.

    Backdated rather than slept through: SQLite's CURRENT_TIMESTAMP has
    one-second resolution.
    """
    stale = datetime(2020, 1, 1)
    with client.app.state.session_factory() as db:
        db.execute(update(DocumentVersion).values(updated_at=stale))
        db.commit()
    assert client.get(f"/api/documents/{DOC}").json()["updated_at"].startswith("2020-01-01")

    client.put(f"{VERSIONS}/1", json={"content": "<p>touched</p>"})

    assert datetime.fromisoformat(client.get(f"/api/documents/{DOC}").json()["updated_at"]) > stale
    # Only this patent was touched.
    assert client.get("/api/documents/2").json()["updated_at"].startswith("2020-01-01")


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("GET", "/api/documents/999", "Document 999 not found."),
        ("GET", "/api/documents/999/versions", "Document 999 not found."),
        ("GET", "/api/documents/999/versions/1", "Document 999 not found."),
        ("GET", "/api/documents/1/versions/99", "Version 99 of document 1 not found."),
        ("POST", "/api/documents/999/versions", "Document 999 not found."),
        ("PUT", "/api/documents/999/versions/1", "Document 999 not found."),
        ("PUT", "/api/documents/1/versions/99", "Version 99 of document 1 not found."),
    ],
)
def test_missing_targets_return_distinct_404s(
    client: TestClient, method: str, path: str, detail: str
) -> None:
    """V5 — the message must distinguish a missing document from a missing version."""
    response = client.request(method, path, json={"content": "<p>x</p>"})

    assert response.status_code == 404
    assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", f"{VERSIONS}/1", {}),
        ("PUT", f"{VERSIONS}/1", {"content": None}),
        ("PUT", f"{VERSIONS}/1", {"content": 5}),
        ("POST", VERSIONS, {}),
        ("POST", VERSIONS, {"content": None}),
        ("POST", VERSIONS, {"content": 5}),
        ("GET", f"{VERSIONS}/0", None),
        ("GET", f"{VERSIONS}/-1", None),
        ("GET", "/api/documents/abc", None),
        # Larger than SQLite's 64-bit INTEGER: unbounded, this reaches the driver
        # and raises OverflowError as a 500.
        ("GET", "/api/documents/99999999999999999999999", None),
        ("GET", f"{VERSIONS}/99999999999999999999999", None),
        ("PUT", f"{VERSIONS}/99999999999999999999999", {"content": "<p>x</p>"}),
    ],
)
def test_invalid_bodies_and_paths_are_422(
    client: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    """V6 — validation is left entirely to FastAPI."""
    assert client.request(method, path, json=body).status_code == 422


def test_empty_content_is_accepted(client: TestClient) -> None:
    """V7 — a user may legitimately clear a draft; "" must round-trip."""
    assert client.put(f"{VERSIONS}/1", json={"content": ""}).status_code == 200
    assert client.get(f"{VERSIONS}/1").json()["content"] == ""

    created = client.post(VERSIONS, json={"content": ""})
    assert created.status_code == 201
    assert created.json()["content"] == ""


def test_oversized_content_is_413(client: TestClient) -> None:
    """V8."""
    cap = get_settings().max_content_bytes
    original = client.get(f"{VERSIONS}/1").json()["content"]
    oversized = "<p>" + "a" * (cap + 1) + "</p>"

    response = client.put(f"{VERSIONS}/1", json={"content": oversized})

    assert response.status_code == 413
    assert str(cap) in response.json()["detail"]
    assert client.get(f"{VERSIONS}/1").json()["content"] == original
    assert version_count(client) == 1

    # The cap is measured in UTF-8 bytes: this is under the character cap and
    # over the byte cap.
    multibyte = "é" * (cap // 2 + 1)
    assert client.post(VERSIONS, json={"content": multibyte}).status_code == 413
    assert version_count(client) == 1


def test_saved_html_is_sanitised(client: TestClient) -> None:
    """V9 — nh3 is wired into both write routes, not merely imported."""
    dangerous = '<p onclick="steal()">keep</p><iframe src="evil"></iframe>'

    put = client.put(f"{VERSIONS}/1", json={"content": dangerous})
    post = client.post(VERSIONS, json={"content": dangerous})

    assert put.json()["content"] == "<p>keep</p>"
    assert post.json()["content"] == "<p>keep</p>"
