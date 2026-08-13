"""V13 — an unexpected server error must still reach the browser as an error.

Without the handler in `create_app`, FastAPI's built-in 500 response is produced
*above* CORSMiddleware and therefore carries no Access-Control-Allow-Origin
header. The browser then reports a CORS failure, the fetch rejects with a
network-level error, and the UI says "cannot reach the server" while the server
is up and the real cause is invisible.
"""

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
