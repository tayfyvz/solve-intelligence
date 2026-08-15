# Backend — FastAPI + SQLAlchemy

Serves the patent documents, their versions, and the AI editing engine.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync                              # install
cp .env.example .env                 # then add your OpenAI API key
uv run uvicorn app.main:app --reload # http://localhost:8000
```

Without an API key everything still runs — only the AI routes need one.
API docs are at `/docs`.

```sh
uv run pytest                                # tests
uv run ruff check . && uv run ruff format .  # lint + format
```

## API

| Method | Path | Notes |
|---|---|---|
| `GET` `POST` | `/api/documents` | list (paged), create |
| `GET` `PATCH` | `/api/documents/{id}` | read, rename |
| `GET` `POST` | `/api/documents/{id}/versions` | list (paged), create new version |
| `GET` | `/api/documents/{id}/versions/{n}` | read one |
| `PUT` | `/api/documents/{id}/versions/{n}` | **save in place** — never creates a version |
| `PATCH` | `/api/documents/{id}/versions/{n}` | rename |
| `DELETE` | `/api/documents/{id}/versions/{n}` | refused (409) if it is the last one |
| `POST` | `/api/ai/chat` | ask a question or propose an edit |
| `POST` | `/api/ai/apply` | apply a proposal the user accepted |
| `POST` | `/api/import/text` | `.txt` → editor HTML |

**Neither AI route nor the import route touches the database.** The handlers take no
`db` parameter at all, so they *cannot* write. Only an explicit save persists anything.

## Database

File-backed SQLite at `data/app.db` (`DATABASE_URL`). On start-up the app creates any
missing tables and, only if there are no documents yet, inserts the two seed patents.
Your work survives a restart. To reset: `rm data/app.db` and restart.

Two tables:

- **`documents`** — id, title, created_at. No content column; a document *is* its versions.
- **`document_versions`** — id, document_id, version_number, name, content (HTML),
  `source` (`user` or `ai`), timestamps.

A version is a **mutable draft**, not a snapshot: saving edits the row in place. Titles and
version names are unique per scope, case-insensitively. Tests use an in-memory database and
never touch the file.

## Layout

```
app
├── main.py         # app factory: CORS, lifespan, routers
├── config.py       # typed settings from env / .env, including every size limit
├── models.py       # Document, DocumentVersion
├── schemas.py      # Pydantic request/response models — the wire contract
├── crud.py         # queries and writes
├── sanitize.py     # nh3 allowlist, applied on the save path
├── textimport.py   # plain .txt -> editor HTML
├── data.py         # seed patents (stored pre-normalised, in TipTap's getHTML() form)
├── db.py           # engine, session factory, SQLite pragmas
├── routers/        # documents.py, ai.py, imports.py
└── ai/             # the editing engine — read in this order
    ├── document.py    # parse() / render() — the round-trip contract
    ├── outline.py     # outline, context and spec building (deterministic retrieval)
    ├── operations.py  # the seven edit operations
    ├── schemas.py     # every model <-> engine contract
    ├── apply.py       # bind -> apply -> renumber once -> remap cross-references
    ├── verify.py      # deterministic check on the produced document
    ├── understand.py  # request classification and its fast path
    ├── summary.py     # one human sentence per operation, for the proposal card
    ├── prompts.py     # prompt text only
    ├── nodes.py       # the seven graph nodes
    ├── llm.py         # the ONLY module that imports `openai`
    └── graph.py       # the ONLY module that imports `langgraph`
```

## How the AI engine is put together

A LangGraph run: `understand` classifies the request, then either
`retrieve → answer` (a question) or `plan_ops` / `draft → judge` (an edit), and every path
ends at `verify`. Each node has its own timeout and the whole graph has a deadline.

The engine is **pure Python over a parsed document**. Everything except `llm.py` and
`graph.py` imports neither `openai` nor `langgraph` — which is why nearly the whole test
suite runs with no API key and no network. A test enforces this by scanning the package in
a fresh subprocess, so a new file is covered the moment it is added.

Two rules the parser depends on: claim **numbers are data, never text** (stripped on parse,
re-injected on render), and operations resolve against the *original* parse, so
"delete claims 3 and 5" deletes what the user saw. Renumbering happens exactly once, at the
end, after which cross-references are remapped.

## Docker

`docker-compose.yml` (repo root) is the **development** stack — it bind-mounts `./server`
and adds `--reload`. The image's own default command is the production shape.

```sh
docker-compose up --build
docker compose down -v   # -v also drops the anonymous .venv volume, which compose reuses
```
