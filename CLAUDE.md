# CLAUDE.md

Guidance for working in this repository. `DESIGN.md` holds the *what and why*; this file holds the
*how* — commands, invariants, and the traps specific to this codebase.

## Project

A patent reviewing application: React + TipTap frontend, FastAPI + SQLAlchemy backend.
Two features are being built on an inherited scaffold — **document versioning** and
**AI-powered editing via chat** (see `DESIGN.md`).

This is an engineering challenge submission. Two consequences that override normal defaults:

- **It will be stress-tested with unexpected inputs.** Degrade gracefully; never fail silently.
- **It will be extended live, in a pair-programming round, without AI.** Every file must be
  explainable in under a minute. Prefer an obvious solution over a clever one, always.

## Commands

```sh
# Backend (from server/)
uv sync                                        # install
uv run uvicorn app.__main__:app --reload       # dev server, :8000
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format .    # lint + format

# Frontend (from client/)
npm install
npm run dev                                    # dev server, :5173
npm run test                                   # vitest
npm run lint
npm run build                                  # tsc + vite build — must pass before submitting

# Whole app
cp server/.env.example server/.env             # required before first run
docker-compose up --build
rm server/data/app.db                          # reset the database
```

## Architecture invariants

Violating any of these is a bug, not a style preference.

1. **`app/ai/document.py` must never import `openai`.** The edit engine is a pure function over
   parsed documents. This is what makes the majority of the test suite runnable without an API key.
2. **`POST /api/ai/edit` must never write to the database.** It returns HTML; only an explicit user
   save persists anything.
3. **The client only calls `setContent` when the AI response `html` is non-null.** A failed or
   ambiguous AI request must leave the document byte-identical.
4. **Claim numbers are a field on `Claim`, never text.** They are stripped on parse and re-injected
   on render. Nothing may derive claim identity from rendered text.
5. **Claim operations resolve against the *original* parse.** Bind numbers to uids before applying
   anything, so `[delete 3, delete 5]` deletes what the user saw.
6. **Renumber exactly once, at the end**, then remap cross-references.
7. **The TipTap editor is uncontrolled.** No effect that compares an HTML string to `getHTML()`.
   Content changes happen by remounting via `key={docId}:{versionNumber}`.
8. **Zustand store holds shared state only** — if only one component reads it, it stays local.
9. **`PUT /versions/{n}` updates in place and never creates a version.** This is challenge
   requirement 3; a test asserts the version count is unchanged.

## Verified environment facts

Confirmed by running the real libraries — do not re-derive or assume otherwise:

- **`openai` 1.109.1.** `client.chat.completions.parse` exists on the **stable** path.
  `openai.resources.beta.chat` **does not exist** — `client.beta.chat.completions.parse` will
  raise. Model is `gpt-5.2-2025-12-11` (a reasoning model: do not send `temperature`).
- **`@tiptap/core` 2.27.1.** `setContent(content, emitUpdate?, parseOptions?, options?)` is
  **positional**. Use `setContent(html, true)` so `onUpdate` fires. There is also an
  `errorOnInvalidContent` option worth enabling.
- **`getHTML()` output is a single line**: no newlines, whitespace collapsed to single spaces,
  `<!DOCTYPE>`/`<head>`/`<title>` stripped, `<h1>Claims</h1>` preserved.
  Patent 1 → 19 `<p>` elements, 8 claims (claim 1 spans 5 paragraphs). Patent 2 → 9 claims.
- **TipTap round-trip is stable**: `parse → getHTML → parse → getHTML` is byte-identical.

## Traps in this codebase

- **The seed in `data.py` is pretty-printed full HTML; production content is collapsed single-line
  body HTML.** These are different strings. The seed must be stored pre-normalised (in `getHTML()`
  form), or the parser will see two shapes.
- **`<title>` is destroyed on the first save** because `getHTML()` returns body content only.
  The title belongs on `Document.title`, never in the content column.
- **TipTap StarterKit's `Paragraph` declares no attributes** — `data-*` markers on paragraphs are
  silently stripped on the round-trip. Structure cannot be smuggled through attributes.
- **`.env` is gitignored and absent.** `docker-compose up` fails until it is created.
- **ESLint 9 with a legacy `.eslintrc.cjs`** and the removed `--ext` flag — `npm run lint` is
  broken until migrated to a flat `eslint.config.js`.
- **The docker bind-mount masks the image's `.venv`.** Needs a named volume, as `node_modules`
  already has.
- **Patent 1 claim 7 references "claim 5" where it means claim 6.** The seed data contains a real
  cross-reference error. Do not "fix" it — it is useful test material.

## Code style

**Python** — type hints on public functions; Pydantic for all API boundaries; no bare `except`;
raise `HTTPException` with a message a user could read. Keep `ai/document.py` composed of small
pure functions; that is where the tests live.

**TypeScript** — no `any`; typed API helpers in `api.ts`; every async action has explicit loading
and error states rendered in the UI, never only `console.error`.

**Comments** explain *why*, not *what*. The claim-parsing and renumbering rules deserve them;
CRUD does not.

## Testing

Tests must justify their existence — target ~20 meaningful ones, not a coverage number.

- The **parse → render → parse round-trip on both seed patents** is the safety net the whole AI
  layer rests on. Write it first, keep it passing.
- The four example instructions from `README.md` are acceptance tests.
- Backend tests use `:memory:` SQLite; AI route tests inject a fake planner function — never a live
  API call.

## Scope discipline

Do not build: Option B (collaboration, presence, CRDT), auth, autosave, version rename/diff/delete,
streaming, agent loops, RAG, persisted chat, or CI. "Not overly complex" is an explicit requirement
of the brief. Ideas beyond scope belong in the `DESIGN.md` future-work section, not in the code.
