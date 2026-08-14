# Patent Reviewer Backend

## Layout

Application code is in the `app/` directory.

```
app
├── __main__.py    # Re-exports `app` so `uvicorn app.__main__:app` keeps working
├── main.py        # FastAPI app factory: CORS, lifespan, router wiring
├── routers/
│   ├── documents.py  # Documents and versions
│   └── ai.py         # POST /api/ai/chat (run 1) and POST /api/ai/apply (run 2).
│                     # Neither handler takes a `db` parameter — the AI surface
│                     # never writes to the database; only an explicit save does.
├── config.py      # Typed settings (pydantic-settings), loaded from env and .env
├── models.py      # DB models (Document, DocumentVersion)
├── schemas.py     # Pydantic request/response models — the wire contract
├── crud.py        # Queries and writes
├── sanitize.py    # nh3 allowlist applied on the save path
├── data.py        # Seed data (stored pre-normalised, in TipTap's getHTML() form)
├── db.py          # Engine, session factory, SQLite pragmas
└── ai/            # The AI editing engine. Read it in this order:
    ├── document.py    # ParsedDocument, parse(), render() — the round-trip contract
    ├── outline.py     # build_outline / build_context / claims_excerpt
    ├── operations.py  # the six operations, and KIND_ORDER
    ├── schemas.py     # every model↔engine contract, and require()
    ├── apply.py       # bind → apply → renumber once → remap cross-references
    ├── verify.py      # the deterministic gate on the produced artefact
    ├── understand.py  # the fast path and gate_understanding
    ├── summary.py     # one human sentence per operation, for the proposal card
    ├── prompts.py     # prompt text only
    ├── llm.py         # THE ONLY MODULE THAT IMPORTS `openai`
    ├── nodes.py       # the seven node functions and node_guard
    └── graph.py       # THE ONLY MODULE THAT IMPORTS `langgraph`
```

The eight engine modules — `document`, `outline`, `operations`, `apply`, `verify`, `schemas`,
`understand`, `summary` — plus `nodes` import **neither `openai` nor `langgraph`**. That is what
makes the great majority of the test suite runnable with no API key and no network, and a test
enforces it by glob in a fresh subprocess.

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
uv run uvicorn app.main:app --reload
```

(`uvicorn app.__main__:app` also works — `__main__.py` is a re-export shim kept so the scaffold's
original documented command still runs. Prefer `app.main:app`: importing a module named `__main__`
as a library is a trap, and it is what the Docker image's own CMD uses.)

### In Docker

`docker-compose.yml` is the **development** stack: it bind-mounts `./server` and overrides the
image's command with `--reload`. Each image's own default is its **production** shape — no
`--reload`, `--proxy-headers`, `--workers 1` — so nothing has to be remembered at deploy time.
See the "Production readiness" section of the root `README.md` for what is and is not production-ready.

```sh
cp server/.env.example server/.env   # then add the key
docker-compose up --build
docker compose down -v               # -v also drops the anonymous .venv/node_modules volumes,
                                     # which compose otherwise REUSES on recreate
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
