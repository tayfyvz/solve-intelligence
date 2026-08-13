# Patent Reviewer Backend

## Layout

Application code is in the `app/` directory.

```
app
├── __main__.py # Re-exports `app` so `uvicorn app.__main__:app` keeps working
├── main.py     # FastAPI app factory: CORS, lifespan, router wiring
├── routers/    # HTTP routes (documents.py)
├── config.py   # Typed settings (pydantic-settings), loaded from env and .env
├── models.py   # DB models (Document, DocumentVersion)
├── schemas.py  # Pydantic request/response models
├── crud.py     # Queries and writes
├── sanitize.py # nh3 allowlist applied on the save path
├── data.py     # Seed data
└── db.py       # Engine, session factory, SQLite pragmas
```

## First-time setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```sh
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

Make sure you create a .env file (see .env.example) with the OpenAI API key we've provided.

## Running locally

To run the backend locally, with auto-reload on code changes,

```sh
uv run uvicorn app.__main__:app --reload
```

## DB

The app uses a **file-backed** SQLite database at `server/data/app.db` (`DATABASE_URL`, default
`sqlite:///./data/app.db`). On start-up it creates any missing tables and, only if the database has
no documents, inserts the seed patents. Your edits and versions therefore survive a restart.

To reset to seed data, delete the file and restart:

```sh
rm server/data/app.db
```

`server/data/` is gitignored. Tests run against an in-memory database instead, so they never touch
this file.

## Tests, lint and format

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```
