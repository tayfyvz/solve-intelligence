"""V10 — the seed is stored in getHTML() shape and seeding is idempotent."""

import re

from fastapi.testclient import TestClient

from app.crud import seed_if_empty
from app.data import SEED_DOCUMENTS

EXPECTED = [
    {"paragraphs": 19, "claims": 8},
    {"paragraphs": 18, "claims": 9},
]


def test_seed_is_normalised_and_idempotent(client: TestClient) -> None:
    page = client.get("/api/documents").json()
    assert page["total"] == len(SEED_DOCUMENTS)

    # Keyed on seed order, not list order: the list is sorted by title.
    for index, expected in enumerate(EXPECTED, start=1):
        content = client.get(f"/api/documents/{index}/versions/1").json()["content"]
        assert "\n" not in content
        assert "<!DOCTYPE" not in content
        assert "<title>" not in content
        assert "<body>" not in content
        assert "> <" not in content
        assert content.startswith("<h1>Claims</h1>")
        assert content.count("<p>") == expected["paragraphs"]
        assert len(re.findall(r"<p>\d+\. ", content)) == expected["claims"]

    # The title survived the move out of <title>, inherited typo included.
    assert client.get("/api/documents/1").json()["title"].endswith("neural activitiies")
    # Seeded versions are named, because the column is NOT NULL.
    assert client.get("/api/documents/1/versions").json()["items"][0]["name"] == "Version 1"

    # Two pieces of deliberate test material the normalisation must not "fix":
    # patent 1's real cross-reference error, and a plain ASCII apostrophe (TipTap
    # does not entity-encode it, so neither may we).
    patent_1 = client.get("/api/documents/1/versions/1").json()["content"]
    assert "7. The method of claim 5," in patent_1
    assert "the system's versatility" in patent_1

    # Idempotence — the guard that keeps a second boot from raising IntegrityError
    # against a file-backed database (C5). The lifespan has already seeded here.
    with client.app.state.session_factory() as db:
        assert seed_if_empty(db) == 0
    assert client.get("/api/documents").json()["total"] == len(SEED_DOCUMENTS)
