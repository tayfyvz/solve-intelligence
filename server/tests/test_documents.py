"""V15 — creating and naming patents and versions.

Names are the only user-supplied identifiers in the app, so every rule about them
(trimmed, non-empty, unique case-insensitively, per-patent for versions) is
pinned here.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import Document, DocumentVersion

DOCUMENTS = "/api/documents"
SEED_TITLE = "Wireless optogenetic device for remotely controlling neural activitiies"


def versions(client: TestClient, document_id: int) -> list[dict[str, object]]:
    return client.get(f"{DOCUMENTS}/{document_id}/versions?limit=100").json()["items"]


def test_create_makes_a_patent_with_exactly_one_named_version(client: TestClient) -> None:
    response = client.post(DOCUMENTS, json={"title": "  Widget  ", "content": "<p>hi</p>"})

    assert response.status_code == 201
    detail = response.json()
    assert detail["title"] == "Widget"  # stored trimmed
    assert detail["version_count"] == 1
    assert detail["latest_version_number"] == 1

    only = versions(client, detail["id"])
    assert [v["version_number"] for v in only] == [1]
    assert only[0]["name"] == "Version 1"
    content = client.get(f"{DOCUMENTS}/{detail['id']}/versions/1").json()["content"]
    assert content == "<p>hi</p>"


def test_create_without_content_starts_an_empty_first_version(client: TestClient) -> None:
    created = client.post(DOCUMENTS, json={"title": "Blank"})

    assert created.status_code == 201
    assert client.get(f"{DOCUMENTS}/{created.json()['id']}/versions/1").json()["content"] == ""


def test_create_sanitises_and_caps_content_like_any_other_write(client: TestClient) -> None:
    """The new route goes through the same gate; it did not grow its own."""
    clean = client.post(
        DOCUMENTS, json={"title": "Scripty", "content": "<p>a</p><script>x</script>"}
    )
    assert (
        client.get(f"{DOCUMENTS}/{clean.json()['id']}/versions/1").json()["content"] == "<p>a</p>"
    )

    oversized = "<p>" + "a" * (get_settings().max_content_bytes + 1) + "</p>"
    response = client.post(DOCUMENTS, json={"title": "Huge", "content": oversized})

    assert response.status_code == 413
    # The rejected patent must not have been half-created.
    assert not any(
        d["title"] == "Huge" for d in client.get(f"{DOCUMENTS}?limit=100").json()["items"]
    )


@pytest.mark.parametrize(
    "title",
    [SEED_TITLE, f"   {SEED_TITLE}   ", SEED_TITLE.upper()],
    ids=["exact", "padded", "cased"],
)
def test_create_rejects_a_duplicate_title(client: TestClient, title: str) -> None:
    """Compared case-insensitively, after trimming."""
    response = client.post(DOCUMENTS, json={"title": title})

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert client.get(f"{DOCUMENTS}?limit=100").json()["total"] == 2


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_create_rejects_blank_and_overlong_titles(client: TestClient, title: str) -> None:
    """Whitespace is stripped before the length checks, so "   " is empty."""
    assert client.post(DOCUMENTS, json={"title": title}).status_code == 422


def test_create_accepts_a_title_at_the_length_limit(client: TestClient) -> None:
    assert client.post(DOCUMENTS, json={"title": "x" * 200}).status_code == 201


def test_rename_patent(client: TestClient) -> None:
    response = client.patch(f"{DOCUMENTS}/1", json={"title": "  Renamed  "})

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert client.get(f"{DOCUMENTS}/1").json()["title"] == "Renamed"
    # Renaming touches no version.
    assert client.get(f"{DOCUMENTS}/1/versions/1").json()["content"].startswith("<h1>Claims</h1>")


def test_rename_patent_to_its_own_title_is_not_a_conflict(client: TestClient) -> None:
    """The self-exclusion in the pre-check: a no-op save must not 409."""
    assert client.patch(f"{DOCUMENTS}/1", json={"title": SEED_TITLE}).status_code == 200
    # Including a pure re-casing of its own title.
    assert client.patch(f"{DOCUMENTS}/1", json={"title": SEED_TITLE.upper()}).status_code == 200


def test_rename_patent_rejects_another_patents_title(client: TestClient) -> None:
    response = client.patch(
        f"{DOCUMENTS}/1", json={"title": "microfluidic device for BLOOD oxygenation"}
    )

    assert response.status_code == 409
    assert client.get(f"{DOCUMENTS}/1").json()["title"] == SEED_TITLE


def test_rename_patent_404s_and_422s(client: TestClient) -> None:
    assert client.patch(f"{DOCUMENTS}/999", json={"title": "x"}).status_code == 404
    assert client.patch(f"{DOCUMENTS}/1", json={"title": "  "}).status_code == 422
    assert client.patch(f"{DOCUMENTS}/1", json={}).status_code == 422


def test_rename_version(client: TestClient) -> None:
    before = client.get(f"{DOCUMENTS}/1/versions/1").json()

    response = client.patch(f"{DOCUMENTS}/1/versions/1", json={"name": "  Filed draft  "})

    assert response.status_code == 200
    assert response.json()["name"] == "Filed draft"
    assert response.json()["content"] == before["content"]  # rename never touches content
    assert client.get(f"{DOCUMENTS}/1").json()["version_count"] == 1


def test_rename_version_conflicts_only_within_its_own_patent(client: TestClient) -> None:
    client.post(f"{DOCUMENTS}/1/versions", json={"content": "<p>v2</p>", "name": "Draft"})

    conflict = client.patch(f"{DOCUMENTS}/1/versions/1", json={"name": "draft"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == 'Version "draft" already exists in this patent.'

    # The same name in a different patent is fine — uniqueness is per patent.
    assert client.patch(f"{DOCUMENTS}/2/versions/1", json={"name": "Draft"}).status_code == 200
    # And renaming a version to the name it already has is a no-op, not a 409.
    assert client.patch(f"{DOCUMENTS}/1/versions/2", json={"name": "Draft"}).status_code == 200


@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        (f"{DOCUMENTS}/999/versions/1", {"name": "x"}, 404),
        (f"{DOCUMENTS}/1/versions/99", {"name": "x"}, 404),
        (f"{DOCUMENTS}/1/versions/1", {"name": "   "}, 422),
        (f"{DOCUMENTS}/1/versions/1", {"name": "x" * 101}, 422),
        (f"{DOCUMENTS}/1/versions/1", {}, 422),
    ],
)
def test_rename_version_rejects_bad_targets_and_names(
    client: TestClient, path: str, body: dict[str, object], expected: int
) -> None:
    assert client.patch(path, json=body).status_code == expected


def test_new_versions_are_auto_named_and_never_collide(client: TestClient) -> None:
    """An omitted name must never be able to fail the request, so it steps around
    names a user has already taken."""
    assert (
        client.post(f"{DOCUMENTS}/1/versions", json={"content": ""}).json()["name"] == "Version 2"
    )
    assert (
        client.post(f"{DOCUMENTS}/1/versions", json={"content": ""}).json()["name"] == "Version 3"
    )

    # A user takes the name the next auto-name would have wanted, twice over.
    client.patch(f"{DOCUMENTS}/1/versions/1", json={"name": "Version 4"})
    client.patch(f"{DOCUMENTS}/1/versions/2", json={"name": "version 4 (2)"})

    created = client.post(f"{DOCUMENTS}/1/versions", json={"content": ""})

    assert created.status_code == 201
    assert created.json()["version_number"] == 4
    assert created.json()["name"] == "Version 4 (3)"


def test_an_explicit_duplicate_version_name_is_a_409(client: TestClient) -> None:
    response = client.post(f"{DOCUMENTS}/1/versions", json={"content": "", "name": "version 1"})

    assert response.status_code == 409
    assert client.get(f"{DOCUMENTS}/1").json()["version_count"] == 1  # nothing was created


@pytest.mark.parametrize(
    "build_row",
    [
        lambda: Document(title=SEED_TITLE.upper()),
        lambda: DocumentVersion(document_id=1, version_number=2, name="VERSION 1", content=""),
    ],
    ids=["patent title", "version name"],
)
def test_the_database_rejects_duplicates_the_pre_check_never_saw(
    client: TestClient, build_row: Callable[[], object]
) -> None:
    """The pre-checks above produce the readable 409; these indexes are what
    survives two writers racing past them. Written through the session rather
    than the API, because that is the only way to skip the pre-check.
    """
    with client.app.state.session_factory() as db, pytest.raises(IntegrityError):
        db.add(build_row())
        db.commit()


def test_put_can_neither_create_nor_rename(client: TestClient) -> None:
    """Invariant 9, restated against the fields PATCH added."""
    client.patch(f"{DOCUMENTS}/1/versions/1", json={"name": "Named"})

    response = client.put(
        f"{DOCUMENTS}/1/versions/1", json={"content": "<p>x</p>", "name": "Other"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Named"  # the extra field is ignored, not applied
    assert client.get(f"{DOCUMENTS}/1").json()["version_count"] == 1
