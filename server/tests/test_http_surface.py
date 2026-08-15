"""The HTTP surface itself, below the routes: HEAD, request bodies, and what a
422 is allowed to say back.

Each of these was found by pointing something other than the app's own client at
the server — a health checker, a fuzzer, a browser preflight. They are the cases
no route handler ever sees, which is exactly why nothing else covers them.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import BODY_TOO_DEEP, BODY_TOO_LARGE, MAX_JSON_DEPTH

DOCUMENTS = "/api/documents"
JSON = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    "path",
    ["/api/health", DOCUMENTS, f"{DOCUMENTS}/1", f"{DOCUMENTS}/1/versions"],
)
def test_head_is_served_wherever_get_is(client: TestClient, path: str) -> None:
    """RFC 9110 requires it and health checkers use it; every GET route answered
    405. The headers must match the GET's — an empty body with a Content-Length of
    0 would tell a caller the resource is empty."""
    head = client.head(path)
    get = client.get(path)

    assert head.status_code == get.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == get.headers["content-length"]
    assert head.headers["content-type"] == get.headers["content-type"]


def test_head_reports_a_missing_resource_like_get_does(client: TestClient) -> None:
    """The method is rewritten, not the routing: a HEAD still gets the real status."""
    assert client.head(f"{DOCUMENTS}/999").status_code == 404


def test_a_deeply_nested_body_is_a_400_not_a_500(client: TestClient) -> None:
    """`json.loads` recurses, so ~1000 nested brackets — a few kilobytes — exhausted
    the stack and every JSON route answered 500. Rejected before the parser sees it."""
    response = client.post(DOCUMENTS, content="[" * 3000 + "]" * 3000, headers=JSON)

    assert response.status_code == 400
    assert response.json()["detail"] == BODY_TOO_DEEP


def test_a_body_at_the_depth_limit_is_still_parsed(client: TestClient) -> None:
    """The guard must not reject bodies the API actually defines. Nesting to the
    limit reaches the route and fails on its schema, not on the guard."""
    nested = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH

    assert client.post(DOCUMENTS, content=nested, headers=JSON).status_code == 422


def test_brackets_inside_strings_are_not_nesting(client: TestClient) -> None:
    """The scanner skips string contents, or a patent whose text contains braces
    would be refused for a structure it does not have."""
    title = "{" * (MAX_JSON_DEPTH * 2) + ' \\" [ ['

    assert client.post(DOCUMENTS, json={"title": title, "content": ""}).status_code == 201


def test_an_oversized_body_is_413_and_the_content_cap_still_answers_itself(
    client: TestClient,
) -> None:
    """The middleware backstop is a multiple of the content cap, so the router's
    own 413 — the one carrying the byte count a user can act on — still runs."""
    cap = get_settings().max_content_bytes

    at_cap = client.put(f"{DOCUMENTS}/1/versions/1", json={"content": "a" * cap})
    over_cap = client.put(f"{DOCUMENTS}/1/versions/1", json={"content": "a" * (cap + 1)})
    huge = client.put(f"{DOCUMENTS}/1/versions/1", content=b"x" * (cap * 2 + 1), headers=JSON)

    assert at_cap.status_code == 200
    assert over_cap.status_code == 413
    assert str(cap) in over_cap.json()["detail"]  # the router's message, not the guard's
    assert huge.status_code == 413
    assert huge.json()["detail"] == BODY_TOO_LARGE


def test_a_422_does_not_echo_the_rejected_input_back(client: TestClient) -> None:
    """The logging policy applies to responses too. FastAPI's default handler puts the
    rejected value in an `input` field, so a 10,000-character title — a customer's
    unpublished patent text on this server — went back over the wire in full. The
    location and the reason stay; the value goes."""
    secret = "ZZQQ-TITLE-SECRET"
    response = client.post(DOCUMENTS, json={"title": secret * 600})

    assert response.status_code == 422
    assert secret not in response.text
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "title"]  # still says WHICH field
    assert detail[0]["msg"]  # and still says why
    assert "input" not in detail[0]
