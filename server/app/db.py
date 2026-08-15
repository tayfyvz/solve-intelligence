"""Engine, session factory and the per-request session dependency."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, StaticPool, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _is_memory_url(url: str) -> bool:
    """`sqlite://` with no path is SQLAlchemy's other spelling of in-memory."""
    parsed = make_url(url)
    database = parsed.database or ""
    return database in ("", ":memory:") or "mode=memory" in database


def _ensure_sqlite_dir(url: str) -> None:
    """`sqlite:///./data/app.db` fails to connect if `data/` does not exist. Parsed with
    make_url rather than sliced: the three-vs-four-slash relative/absolute distinction is
    easy to get wrong."""
    parsed = make_url(url)
    if not parsed.database:
        return
    parent = Path(parsed.database).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(url: str) -> Engine:
    """Build an engine with the pragmas the app relies on.

    Exposed so tests can build a `:memory:` engine with identical wiring rather
    than duplicating it.
    """
    kwargs: dict[str, object] = {}
    # check_same_thread is a sqlite3-only connect argument and psycopg raises TypeError
    # on it, so "the models port to Postgres by changing a URL" needs this guard to be
    # true. Keyed on the URL rather than engine.dialect.name because the answer is needed
    # before create_engine is called.
    if make_url(url).get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_url(url):
            # A shared in-memory database has no file to share, so without StaticPool
            # every pooled connection would see its own empty database.
            kwargs["poolclass"] = StaticPool
        else:
            _ensure_sqlite_dir(url)

    engine = create_engine(url, **kwargs)

    # Pragmas are per-connection, so they belong on the connect event. Guarded on
    # the dialect: fired unconditionally, this crashes on first connect to
    # anything that is not SQLite.
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            # Not on by default; this is what makes ondelete="CASCADE" real.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Two browser tabs saving at once should wait, not fail instantly.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


engine: Engine = create_db_engine(get_settings().database_url)
SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, autoflush=False)


def get_db(request: Request) -> Iterator[Session]:
    """Resolve the factory from the app so tests and the lifespan share one
    mechanism (dependency_overrides alone would not cover the lifespan)."""
    with request.app.state.session_factory() as session:
        yield session


def init_db(bind: Engine) -> None:
    """Create any missing tables. `app.models` must be imported first, or the
    metadata is empty."""
    Base.metadata.create_all(bind=bind)
