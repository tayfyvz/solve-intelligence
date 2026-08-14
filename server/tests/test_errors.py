"""V13 — an unexpected server error must still reach the browser as an error.

Without the handler in `create_app`, FastAPI's built-in 500 response is produced
*above* CORSMiddleware and therefore carries no Access-Control-Allow-Origin
header. The browser then reports a CORS failure, the fetch rejects with a
network-level error, and the UI says "cannot reach the server" while the server
is up and the real cause is invisible.
"""

import logging

from fastapi.testclient import TestClient

ORIGIN = "http://localhost:5173"


def test_unhandled_errors_return_a_readable_500_with_cors_headers(client: TestClient) -> None:
    @client.app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    response = client.get("/api/boom", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    # A sentence, so the client's error formatter has something to render.
    assert response.json() == {"detail": "Something went wrong on the server."}
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_normal_responses_still_carry_cors_headers(client: TestClient) -> None:
    """The guard above must not have displaced CORS from the happy path."""
    response = client.get("/api/documents", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_cors_is_uncredentialed_and_narrow(client: TestClient) -> None:
    """PLAN §28.2.3 — ship-blocking. There is no auth, no cookie and no
    Authorization header anywhere in the client, so `allow_credentials=True` bought
    nothing and told browsers to attach ambient credentials cross-origin. A
    preflight is the only place the negotiated policy is observable."""
    response = client.options(
        "/api/documents",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 200
    assert "access-control-allow-credentials" not in response.headers
    allowed = {m.strip() for m in response.headers["access-control-allow-methods"].split(",")}
    assert allowed == {"GET", "POST", "PUT", "DELETE"}
    assert "*" not in response.headers["access-control-allow-headers"]


def test_the_unhandled_error_log_line_carries_no_exception_message(client, caplog) -> None:
    """PLAN §28.2.5 — ship-blocking. `logger.exception` renders the traceback, whose
    last line is str(exc); a ValidationError's message quotes the input that failed,
    and on this server that input is a customer's unpublished patent text. The line
    logs the exception TYPE and the path, and nothing that has been near a document."""
    secret = "A claim-1 fragment that must never be logged"

    @client.app.get("/api/boom-secret")
    def boom() -> None:
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        assert client.get("/api/boom-secret").status_code == 500

    logged = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(
        r.exc_text or "" for r in caplog.records
    )
    assert secret not in logged
    assert "RuntimeError" in logged  # the type IS logged — that is the whole point
    assert "/api/boom-secret" in logged
