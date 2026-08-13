"""The cross-language drift guard for the HTTP contract.

Every client test mocks `../api` or axios, so no frontend test can notice that a
route moved or a field was renamed on this side. This reads the two files that
define the client's half of the wire — `api.ts` (routes) and `types.ts` (shapes)
— and asserts they still line up with the server's own OpenAPI document.

Rename `version_number` on either side and this goes red, naming the field and
the side that lost it.
"""

import re
from pathlib import Path

from app.main import create_app

CLIENT = Path(__file__).parents[2] / "client/src"
API_TS = CLIENT / "api.ts"
TYPES_TS = CLIENT / "types.ts"

# Task 2 (AI editing via chat) is not implemented: `aiEdit`, `AiEditRequest` and
# `AiEditResponse` exist on the client but /api/ai/edit does not exist on the
# server. They are excluded here rather than silently unmatched.
# DELETE THIS EXCLUSION when the route lands — test_ai_surface_is_still_unbuilt
# fails the moment it does, so the reminder is enforced, not written down.
AI_ROUTE = "/api/ai/edit"
AI_TYPES = frozenset({"AiEditRequest", "AiEditResponse", "ChatTurn"})

# `${id}` in a client URL is `{document_id}` in the OpenAPI path. An explicit map
# (rather than blanking every placeholder) keeps a renamed path parameter visible.
PATH_PARAMS = {"id": "document_id", "versionNumber": "version_number"}

# What we expect to extract. A regex that quietly matches nothing would make this
# whole file pass while guarding nothing, so the counts are asserted too.
EXPECTED_ROUTES = 5  # list, get document, get version, create version, update version
EXPECTED_TYPES = {
    "DocumentSummary",
    "DocumentDetail",
    "VersionSummary",
    "VersionRead",
    "VersionWrite",
}

_CALL = re.compile(r"\b\w*[Hh]ttp\.(get|post|put|delete|patch)\(\s*[`\"']([^`\"']*)[`\"']")
_INTERFACE = re.compile(r"^export interface (\w+) \{$(.*?)^\}$", re.M | re.S)
_FIELD = re.compile(r"^\s{2}(\w+)\??:", re.M)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def _client_routes() -> set[tuple[str, str]]:
    """(verb, openapi-path) for every axios call in api.ts."""
    calls = _CALL.findall(API_TS.read_text())
    assert calls, f"No axios calls matched in {API_TS} — the guard would pass on nothing."

    routes = set()
    for verb, url in calls:
        if url == AI_ROUTE:
            continue
        for name in _PLACEHOLDER.findall(url):
            assert name in PATH_PARAMS, (
                f"{API_TS}: unknown path placeholder ${{{name}}} in {url}. "
                f"Add it to PATH_PARAMS with its OpenAPI name."
            )
        routes.add((verb, _PLACEHOLDER.sub(lambda m: f"{{{PATH_PARAMS[m.group(1)]}}}", url)))
    return routes


def _client_types() -> dict[str, set[str]]:
    """Interface name -> field names, from types.ts."""
    source = _BLOCK_COMMENT.sub("", TYPES_TS.read_text())
    types = {
        name: set(_FIELD.findall(body))
        for name, body in _INTERFACE.findall(source)
        if name not in AI_TYPES
    }
    for name, fields in types.items():
        assert fields, f"{TYPES_TS}: parsed interface {name} with zero fields — regex drift."
    return types


def test_client_routes_exist_on_the_server() -> None:
    paths = create_app().openapi()["paths"]
    routes = _client_routes()

    assert len(routes) == EXPECTED_ROUTES, (
        f"Expected {EXPECTED_ROUTES} non-AI routes in {API_TS}, extracted {len(routes)}: "
        f"{sorted(routes)}. Update EXPECTED_ROUTES if the client really changed."
    )
    for verb, path in sorted(routes):
        assert path in paths, (
            f"Client calls {verb.upper()} {path} but the server has no such path. "
            f"Server paths: {sorted(paths)}"
        )
        assert verb in paths[path], (
            f"Client calls {verb.upper()} {path} but the server only serves "
            f"{sorted(m.upper() for m in paths[path])} there."
        )


def test_client_types_match_the_server_schemas() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    types = _client_types()

    assert set(types) == EXPECTED_TYPES, (
        f"Extracted {sorted(types)} from {TYPES_TS}, expected {sorted(EXPECTED_TYPES)}. "
        f"A new wire type must be added to EXPECTED_TYPES (or to AI_TYPES if it is Task 2)."
    )
    for name, client_fields in sorted(types.items()):
        assert name in schemas, f"{TYPES_TS} declares {name}, which no server schema matches."
        server_fields = set(schemas[name]["properties"])
        assert client_fields == server_fields, (
            f"{name} drifted. "
            f"On the client but not the server: {sorted(client_fields - server_fields) or 'none'}. "
            f"On the server but not the client: {sorted(server_fields - client_fields) or 'none'}."
        )


def test_ai_surface_is_still_unbuilt() -> None:
    """The tripwire on the exclusion above: Task 2 landing must not stay unguarded."""
    assert AI_ROUTE not in create_app().openapi()["paths"], (
        f"{AI_ROUTE} now exists. Remove AI_ROUTE/AI_TYPES from this file so the AI "
        f"request and response shapes are covered by the contract guard too."
    )
