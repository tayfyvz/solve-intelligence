import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
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
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500, content={"detail": "Something went wrong on the server."}
            )

    # Explicit origins, not ["*"]: "*" with allow_credentials=True is invalid per
    # the CORS spec and browsers reject the pairing outright.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(documents.router)
    application.include_router(ai.router)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
