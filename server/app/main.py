import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

# Imported for its side effect: importing the models registers the tables on
# Base.metadata. Without it create_all() has nothing to create.
import app.models  # noqa: F401
from app.config import get_settings
from app.crud import seed_if_empty
from app.db import SessionLocal, init_db
from app.routers import ai, documents

logger = logging.getLogger(__name__)

# Deeper than any body this API defines (the deepest is a chat request's list of
# operations, at 3) and far below CPython's ~1000-frame recursion limit, which
# `json.loads` hits — as an uncatchable-looking RecursionError, i.e. a 500 — on a
# body of a few kilobytes.
MAX_JSON_DEPTH = 64

# Opening/closing brackets, as bytes, for the depth scan below.
_JSON_OPEN = frozenset(b"{[")
_JSON_CLOSE = frozenset(b"}]")
_QUOTE, _BACKSLASH = ord('"'), ord("\\")

BODY_TOO_DEEP = "That request body is nested too deeply to parse."
BODY_TOO_LARGE = "That request body is too large."


def _exceeds_json_depth(body: bytes, limit: int) -> bool:
    """True if `body` nests brackets more than `limit` deep, ignoring strings.

    Scanned rather than parsed, because parsing is the thing that blows the stack.
    The cheap `count` prefilter runs first: total open brackets is an upper bound
    on depth, and every real body here is far under the limit, so the byte loop —
    which is slow on a megabyte of patent HTML — almost never runs.
    """
    if body.count(b"{") + body.count(b"[") <= limit:
        return False

    depth = 0
    in_string = False
    escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == _BACKSLASH:
                escaped = True
            elif byte == _QUOTE:
                in_string = False
        elif byte == _QUOTE:
            in_string = True
        elif byte in _JSON_OPEN:
            depth += 1
            if depth > limit:
                return True
        elif byte in _JSON_CLOSE:
            depth -= 1
    return False


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    # The session factory comes from the app, not from module state, so tests
    # get an in-memory database here too — the lifespan runs outside the request
    # cycle, where dependency_overrides would not reach it.
    with application.state.session_factory() as db:
        init_db(db.get_bind())
        seed_if_empty(db)
    yield


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    application = FastAPI(title="Patent Editor API", lifespan=lifespan)
    application.state.session_factory = session_factory or SessionLocal

    # Registered BEFORE CORSMiddleware so that CORS ends up wrapping it: Starlette
    # applies the most recently added middleware outermost. This is deliberate.
    # FastAPI's built-in 500 handling sits above *all* user middleware, so an
    # unhandled exception produces a response with no Access-Control-Allow-Origin
    # header; the browser then reports a CORS failure and the UI says "cannot
    # reach the server" while the server is fine. Catching it here keeps the
    # response inside CORS, so the client gets a sentence it can render.
    @application.middleware("http")
    async def handle_unexpected_errors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            # The TYPE and the path, never str(exc): a ValidationError's message quotes
            # the input that failed, and on this server that input is a customer's
            # unpublished patent text (PLAN §28.2.5). logger.exception would render the
            # traceback, whose final line is that same message.
            logger.error(
                "http.unhandled type=%s method=%s path=%s",
                type(exc).__name__,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500, content={"detail": "Something went wrong on the server."}
            )

    # Registered after the handler above so it sits OUTSIDE it: the whole point is
    # to answer before the body reaches a parser, and the 500 handler is what would
    # otherwise catch the fallout.
    max_body_bytes = settings.max_content_bytes * 2  # JSON overhead on the largest save

    @application.middleware("http")
    async def guard_request_bodies(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            declared = request.headers.get("content-length", "")
            # Checked from the header first, so an oversized body is refused
            # without being read into memory.
            if declared.isdigit() and int(declared) > max_body_bytes:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": BODY_TOO_LARGE},
                )
            # Reading here is safe: Starlette's BaseHTTPMiddleware caches the body
            # and replays it to the route.
            body = await request.body()
            if len(body) > max_body_bytes:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": BODY_TOO_LARGE},
                )
            if _exceeds_json_depth(body, MAX_JSON_DEPTH):
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST, content={"detail": BODY_TOO_DEEP}
                )
        return await call_next(request)

    # RFC 9110: HEAD is required wherever GET is served, and health checkers use it.
    # Done once here rather than by adding `methods=["GET", "HEAD"]` to every route,
    # so a GET route added later is covered the day it is added. The body is
    # generated and discarded — that is what makes the headers, Content-Length
    # included, identical to the GET's.
    @application.middleware("http")
    async def serve_head_like_get(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "HEAD":
            return await call_next(request)
        request.scope["method"] = "GET"
        response = await call_next(request)
        async for _ in response.body_iterator:  # type: ignore[attr-defined]
            pass
        return Response(status_code=response.status_code, headers=response.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's default echoes the rejected value back under `input`. On this
        server that value is a customer's patent text or title, so the response
        would put over the wire exactly what the 500 handler above is careful to
        keep out of the logs. The caller already knows what it sent; it needs the
        location and the reason."""
        errors = [
            {key: value for key, value in error.items() if key != "input"} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder({"detail": errors}),
        )

    # Explicit origins, not ["*"]: "*" with allow_credentials=True is invalid per
    # the CORS spec and browsers reject the pairing outright.
    application.include_router(documents.router)
    application.include_router(ai.router)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Derived from the routes rather than typed out: the hand-written list said
    # GET/POST/PUT/DELETE, which allowed a verb no route serves and omitted the
    # PATCH both rename features use — so every rename failed preflight and the UI
    # reported "cannot reach the server". A derived list cannot drift again.
    # OPTIONS is the preflight itself; HEAD is served by the middleware above.
    served_methods = sorted(
        {method for route in application.routes for method in getattr(route, "methods", ())}
        | {"OPTIONS", "HEAD"}
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # There is no auth, no cookie and no Authorization header anywhere in the
        # client, so credentialed CORS buys nothing and only widens the surface — it
        # is the flag that tells a browser to attach ambient credentials to a
        # cross-origin request. Flip it back to True in the SAME commit that
        # introduces authentication, never before.
        allow_credentials=False,
        allow_methods=served_methods,
        # The one header the client actually issues.
        allow_headers=["Content-Type"],
    )

    return application


app = create_app()
