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
uv run uvicorn app.main:app --reload           # dev server, :8000 (__main__ is a shim)
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

1. **The eight engine modules import neither `openai` nor `langgraph`.** Those are `document`,
   `outline`, `operations`, `apply`, `verify`, `schemas`, `understand` and `summary` — plus
   `nodes.py`, which is held to the same rule. The engine is pure functions over parsed documents.
   This is what makes the great majority of the test suite runnable with no API key and no network.
   `llm.py` is the only module that imports `openai`; `graph.py` is the only one that imports
   `langgraph`. **T5 enforces it by glob in a fresh subprocess**, so a new file under `app/ai/` is
   covered the moment it is added rather than when someone remembers to list it.
2. **Neither AI route writes to the database.** `POST /api/ai/chat` (run 1) and
   `POST /api/ai/apply` (run 2) return HTML; only an explicit user save persists anything. The
   mechanism is not discipline: **neither handler takes a `db` parameter**, so there is nothing to
   write with. R13 asserts it against the live signatures.
3. **The client only calls `setContent` when the AI response `html` is non-null.** A failed or
   ambiguous AI request must leave the document byte-identical.
4. **Claim numbers are a field on `Claim`, never text.** They are stripped on parse and re-injected
   on render. Nothing may derive claim identity from rendered text.
5. **Claim operations resolve against the *original* parse.** Bind numbers to uids before applying
   anything, so `[delete 3, delete 5]` deletes what the user saw.
6. **Renumber exactly once, at the end**, then remap cross-references.
7. **The TipTap editor is uncontrolled.** No effect that compares an HTML string to `getHTML()`.
   Content changes happen by remounting via `key={docId}:{versionNumber}`.
8. **Zustand store holds shared state only — with one named exception.** If only one component reads
   a value, it stays local. The exception is a value that must be written *atomically with* an
   existing store field, where recording it anywhere else would record it at a different moment —
   which is the bug, not the design. There is exactly one: **`versionSource: "user" | "ai" | null`**,
   written in the same `set()` as `versionNumber` and read once, with `getState()`, by `ChatPanel`'s
   version-change effect. It is not shared state; it is a property of a store transition, and the
   store is the only thing that can observe the transition. **Adding to this list requires the same
   justification — atomicity, not convenience — and the reason must be a comment on the field.**
9. **`PUT /versions/{n}` updates in place and never creates a version.** This is challenge
   requirement 3; a test asserts the version count is unchanged.

## Verified environment facts

Confirmed by running the real libraries — do not re-derive or assume otherwise:

- **`openai` 1.109.1.** `client.chat.completions.parse` exists on the **stable** path, and that is
  what `llm.py` calls. Model is `gpt-5.2-2025-12-11`; `client.models.retrieve` resolves it
  (`owned_by=system`). *(An earlier version of this file said `client.beta.chat.completions.parse`
  "will raise". **That was wrong** — C21: the module `openai.resources.beta.chat` is indeed gone,
  but `client.beta.chat` is a live alias and `.parse` is a working bound method. The design is
  unaffected, because we use the stable path either way; only the justification was false, and a
  false justification defended out loud is worse than none.)*
- **`temperature` and `reasoning_effort` are MUTUALLY EXCLUSIVE on `gpt-5.2-2025-12-11`, and that
  is the fact that matters.** Measured 2026-08-14 (PLAN §27.4 correction 40):

  | `reasoning_effort` | `temperature` | Result |
  |---|---|---|
  | `"low"` | omitted | accepted |
  | `"low"` | `1.0` | accepted |
  | absent | `0.0` | accepted |
  | `"low"` | `0.0` | **400 — "does not support 0.0 with this model"** |

  4Z measured each parameter **on its own** and recorded both as accepted; both of those
  measurements are correct, and the combination was never tried. The shipped config used exactly
  the failing pair, so `understand`, `plan_ops` and `judge` returned 400 on every live call.
  **The resolution: `openai_reasoning_effort` defaults to `None`.** `reasoning_effort` is the one
  dropped because 4Z also measured `reasoning_tokens == 0` on all 14 calls — it buys nothing here —
  while `temperature=0` is load-bearing for PLAN §21.2's deliberate split: `temperature=0` on the
  deterministic-output nodes (`understand`, `plan_ops`, `judge`), omitted on `draft`/`answer`.
  **Re-enabling `reasoning_effort` requires clearing those three temperatures in the same change**;
  tests `L11` and `L12` fail if you do only one of the two.
  *(An earlier version of this file said "a reasoning model: do not send `temperature`". That was
  an assumption, and it was wrong for the stated reason — but it happened to describe a working
  configuration, which is why the correction to it did not surface this until a live click-through.)*
- **Reasoning tokens are zero on this model.** `usage.completion_tokens_details.reasoning_tokens`
  was **0 on all 14 measured calls**; completion tokens ranged 74–129. `max_completion_tokens`
  ceilings are therefore bounded by the *visible* answer only — keep them generous because they
  are free, not because a reasoning pass eats them.
- **Measured latency for one `chat.completions.parse` call** (14 live calls, 2026-08-13, the real
  schemas over the seed outline): **min 1.1 s, median 1.5 s, max 6.7 s** (the 6.7 s was an
  `insert_section` request carrying prior-art text). The five-call worst case at the observed max
  is ~33 s. `reasoning_effort="low"` is accepted **on its own** — but see the mutual-exclusion row
  above before setting it; it is shipped as `None`.
- **`@tiptap/core` 2.27.1.** `setContent(content, emitUpdate?, parseOptions?, options?)` is
  **positional**. Use `setContent(html, true)` so `onUpdate` fires — dropping that `true` is the
  highest-severity one-character bug in this feature. *(An earlier version recommended enabling
  `errorOnInvalidContent`. It was **cut** in PLAN §1.1: without an `onContentError` handler it is a
  no-op that silently drops content from a stored version, which is worse than absent. The
  `try/catch` around `setContent` is the real guard and stays.)*
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
- **`.env` is gitignored and absent.** `docker-compose up` fails until it is created — compose
  resolves `env_file` up front. Still true, and deliberately so: it is the first line of the README.
- **ESLint 9 — FIXED.** Migrated to a flat `eslint.config.js` and `npm run lint` is clean. *(The
  original note blamed "the removed `--ext` flag". **C22: `--ext` is not removed** — eslint 9.39.2
  still accepts it as an inert no-op under flat config. The only real blocker was the missing
  `eslint.config.js`. The trap worth remembering is the opposite one: dropping `--ext` **without**
  a `files: ['**/*.{ts,tsx}']` entry gives a green lint that checks nothing.)*
- **The docker bind-mount masks the image's `.venv` — FIXED** with an anonymous volume
  (`- /usr/src/app/.venv`), as `node_modules` already had. Compose **reuses** anonymous volumes on
  recreate, so `docker compose down -v` is the escape hatch when dependencies change.
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

Do not build: Option B (collaboration, presence, CRDT), auth, autosave, version **diff or
delete**, streaming, agent loops, RAG, persisted chat, or CI. *(This list used to ban version
"rename/diff/delete". **Version rename shipped in Task 1** and is in the UI — a scope rule that
forbids something already built teaches the reader to distrust the whole list.)* "Not overly complex" is an explicit requirement
of the brief. Ideas beyond scope belong in the `DESIGN.md` future-work section, not in the code.
