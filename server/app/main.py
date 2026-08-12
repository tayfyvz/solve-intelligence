from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

# Imported for its side effect: importing the models registers the tables on
# Base.metadata. Without it create_all() has nothing to create.
import app.models  # noqa: F401
from app.config import get_settings
from app.crud import seed_if_empty
from app.db import SessionLocal, init_db
from app.routers import documents


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
    settings = get_settings()
    application = FastAPI(title="Patent Editor API", lifespan=lifespan)
    application.state.session_factory = session_factory or SessionLocal

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

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
