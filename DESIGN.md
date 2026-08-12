# Design & Implementation Plan

**Project:** Solve Intelligence engineering challenge — patent reviewing application  
**Scope:** Task 1 (document versioning) + Task 2 **Option A** (AI-powered document editing)  
**Principle:** Clear, maintainable, not overly complex — every piece must earn its keep.  
**Status:** Verified by multi-agent senior review — ready to implement.

---

## 0. Read this first

Two decisions carry the whole design:

1. **The LLM never writes HTML.** It returns structured edit operations (claim numbers + plain text). Deterministic Python applies them, renumbers once, sanitises, and returns TipTap-safe HTML. TipTap’s `setContent` accepts empty/`""` without error and can clear the editor — a truncated “full HTML” model response would wipe the patent and look like success.
2. **Versions are mutable drafts.** Create copies a source version; Save updates the selected version in place. Requirement 3 explicitly forbids “save = new snapshot.”

**Demo script (60 seconds):** open Patent 1 → create Version 2 → edit Version 1 and Save → switch to Version 2 (unchanged) → chat “Make claim 1 bold” → drop a `.txt` and ask for a Background → Save.

**Acceptance golden path:** the four README examples work on seed Patent 1, including multi-paragraph bold on claim 1; AI failure never changes the editor; Save persists only when the user clicks Save.

---

## Table of contents

1. [Challenge understanding](#1-challenge-understanding)
2. [Inherited codebase audit](#2-inherited-codebase-audit)
3. [Architecture overview](#3-architecture-overview)
4. [Task 1 — Document versioning](#4-task-1--document-versioning)
5. [Task 2 Option A — AI editing](#5-task-2-option-a--ai-editing)
6. [Frontend](#6-frontend)
7. [Cross-cutting concerns](#7-cross-cutting-concerns)
8. [Testing](#8-testing)
9. [Tooling & Docker](#9-tooling--docker)
10. [Implementation plan](#10-implementation-plan)
11. [Out of scope & future work](#11-out-of-scope--future-work)
12. [Submission checklist](#12-submission-checklist)
13. [Verification log](#13-verification-log)

---

## 1. Challenge understanding

### 1.1 Sources of truth

| File | Role |
|---|---|
| `README.md` | Tasks, Docker, Objective, write-up location (“end of **this file**”) |
| `CHALLENGE.md` | Company framing, tech stack, “any approach”, “not overly complex” |

The improvements write-up goes at the end of **`README.md`**, not `CHALLENGE.md`. Do not treat this design doc as that write-up.

### 1.2 Must ship

**Global**

- Production-quality, readable code; unit tests; no obvious bugs
- `docker-compose up --build` works with `server/.env` from `.env.example`
- Short improvements blurb at end of `README.md`

**Task 1 — Versioning (always required)**

1. Create new versions  
2. Switch between existing versions  
3. Edit any existing version and **save without creating a new version**

**Task 2 Option A — AI editing**

1. Chat-style UI for natural-language edit instructions  
2. AI interprets instruction and modifies document HTML  
3. Changes apply to the editor immediately  
4. Drag-and-drop `.txt` into chat for extra AI context  

**Examples that must work (golden tests)**

| Instruction | Capability probed |
|---|---|
| “Make claim 1 bold” | Format **every** block of a multi-`<p>` claim |
| “Delete claim 3” | Structural delete + renumber prefixes |
| “Add a new dependent claim after claim 2 that specifies the material is glass” | Insert + dependent phrasing + renumber |
| “Write a background section based on the prior art file I have uploaded” | Grounded generation + section into Claims-only seed |

### 1.3 Explicitly not Option A

Live collaboration, presence, CRDTs, conflict resolution → **Option B only**. Skip entirely.

### 1.4 Quality bar

- Stress-test resilience (reviewers will probe common behaviours)
- Explainable in live pair-programming without AI
- Patent/claim awareness (multi-paragraph claims, numbering)
- Clarity over cleverness

### 1.5 Ambiguities — decided

| Ambiguity | Decision |
|---|---|
| LLM returns HTML vs ops? | **Ops** (see §5) |
| Where mutation runs? | **Server** (Python, unit-testable) |
| Auto-save after AI? | **No** — apply to editor, mark dirty, user Saves |
| Chat persistence? | **Session-only** in the UI |
| File attachment? | **Client sticky until cleared**; re-sent each AI request; server does not store |
| Renumber after delete/insert? | **Yes** — claim prefixes only; warn that cross-refs may need review |
| Ambiguous instruction? | `needs_clarification`; no silent wrong edit |
| Keep `/document` `/save`? | **Replace** with version-aware `/api/...` routes |

---

## 2. Inherited codebase audit

The scaffold is a junior mock-up: TipTap load/edit/save of two patents. **No versioning, no AI UI, no OpenAI calls, no tests.**

### 2.1 Stack already chosen (keep)

| Layer | Choice |
|---|---|
| Client | React 18, TypeScript, Vite, Tailwind 4, TipTap 2 + StarterKit, axios |
| Server | FastAPI, SQLAlchemy 2, Pydantic 2, uvicorn, Python ≥3.13 |
| LLM SDK | `openai` declared but unused |
| DB today | In-memory SQLite + `StaticPool` (must change — A2) |
| Ops | Docker Compose (client `:5173`, server `:8000`) |

### 2.2 Defects that shape the design

| ID | Severity | Issue | Design response |
|---|---|---|---|
| A1 | Critical | Seed stores full HTML; TipTap `getHTML()` strips doctype/head/`<title>` on first save | Store TipTap body fragments; `Document.title` holds the title |
| A2 | Critical | `:memory:` + `StaticPool` shares one connection across sessions — unsafe under concurrent requests | File-backed SQLite |
| A3 | Critical | `GET /document/{id}` → 500 when missing | Explicit 404 |
| A4 | Critical | `POST /save/{id}` returns 200 when 0 rows updated; `currentDocumentId` starts at `0` | Rowcount → 404; never save id 0 |
| A5 | High | Controlled sync: full-HTML seed ≠ `getHTML()`, so load/switch can re-`setContent` and reset the caret | Uncontrolled TipTap; remount via `key` |
| A6 | High | TipTap v2 `setContent` default `emitUpdate=false` → React/editor diverge | AI apply: `setContent(html, true)` (positional) |
| A7 | High | No request sequencing on patent switch | Monotonic request token |
| A8 | High | Switch discards unsaved edits silently | Dirty guard (Save / Discard / Cancel) |
| A9 | Med | Errors only in `console.error` | Visible inline/toast error |
| A10 | High | ESLint 9 + `.eslintrc.cjs` → `npm run lint` broken | Flat `eslint.config.js` (phase 5) |
| A11 | Med | Seed inserts fixed ids every lifespan | Idempotent seed |
| A12 | Med | Compose requires gitignored `.env` | Document `cp .env.example .env`; app starts without key |
| A13 | Med | Server bind-mount masks image `.venv` | Named Docker volume for `/usr/src/app/.venv` (default) |

### 2.3 Seed data facts

- Patent 1: 8 claims; claim 1 spans **5** `<p>` tags  
- Patent 2: 9 claims; similar multi-paragraph pattern  
- Only a **Claims** section exists — Background must be **inserted**  
- Claim numbers are text prefixes (`1. `), not HTML attributes  
- TipTap StarterKit `Paragraph` declares **no attributes** — `data-id` is stripped  

---

## 3. Architecture overview

### 3.1 High-level flow

```
Browser
  DocumentList · VersionBar · Editor (TipTap) · ChatPanel (DnD inside)
           │                         │                │
           └──────── api.ts ─────────┴────────────────┘
                              │ HTTP JSON
Server
  documents router ── crud / models ── SQLite (file)
  ai router ── ai/edit.py (planner) + ai/claims.py (parse/apply/render)
               sanitize (nh3)
```

**Save:** editor HTML → `PUT /api/documents/{id}/versions/{n}` → sanitise → update row.

**AI edit:** current HTML + instruction + optional context + last 3 turns → `POST /api/ai/edit` → ops → apply → return HTML. **Never writes the DB.**

### 3.2 Target layout (lean)

```
solve-intelligence/
├── DESIGN.md, README.md, CHALLENGE.md, docker-compose.yml
├── .gitignore                          # includes server/data/
├── server/
│   ├── app/
│   │   ├── __main__.py                 # re-export app for uvicorn
│   │   ├── main.py                     # FastAPI, CORS, routers, lifespan
│   │   ├── config.py                   # Settings from env
│   │   ├── db.py                       # engine, session, pragmas, ensure data dir
│   │   ├── models.py                   # Document, DocumentVersion
│   │   ├── schemas.py                  # API + EditPlan models
│   │   ├── crud.py                     # document/version queries
│   │   ├── sanitize.py                 # nh3 allowlist
│   │   ├── data.py                     # seed bodies + titles
│   │   ├── routers/documents.py
│   │   ├── routers/ai.py
│   │   └── ai/
│   │       ├── claims.py               # parse / apply / renumber / render
│   │       └── edit.py                 # OpenAI call + orchestrate apply
│   └── tests/                          # pytest discovers server/tests
└── client/src/
    ├── App.tsx
    ├── api.ts, types.ts
    ├── useDocument.ts
    ├── contextFile.ts
    └── components/
        Editor.tsx, ChatPanel.tsx
        DocumentList.tsx, VersionBar.tsx
```

**AI is two files only** (`claims.py` + `edit.py`). No Protocol layer, no separate `service.py` / `schema.py` — EditPlan lives in `schemas.py`. Fold file-drop UI into `ChatPanel`. Simple CSS/Tailwind loading + error text; remove Emotion/`LoadingOverlay` when unused.

**Why routers/:** FastAPI’s documented layout; `__main__.py` re-exports so `uvicorn app.__main__:app` stays unchanged. Update `server/README.md`.

### 3.3 Technology choices

| Choice | Why |
|---|---|
| TipTap (keep) | Already in scaffold; company dislikes TinyMCE |
| OpenAI Chat Completions + Structured Outputs | `openai` already in deps |
| BeautifulSoup `html.parser` | Parse/render claims; no lxml |
| `nh3` allowlist | Sanitize to TipTap schema |
| File SQLite | Fixes A2; `DATABASE_URL` configurable |
| `pydantic-settings` | Typed env config |
| pytest + Vitest | Brief wants unit tests |
| No CI for MVP | Local green first |

---

## 4. Task 1 — Document versioning

### 4.1 Data model

```python
class Document(Base):
    __tablename__ = "document"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number",
    )

class DocumentVersion(Base):
    """Mutable draft. Save updates this row; it never creates a sibling."""
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int]
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
```

**Invariant:** every document has ≥1 version. Content lives **only** on `DocumentVersion`.  
No version `name` field in MVP (cut for simplicity).

### 4.2 Why not alternatives

| Alternative | Reject |
|---|---|
| Single table | Collapses identity and draft |
| Live `Document.content` + snapshots | **Breaks requirement 3** |
| Server `current_version_id` | Viewer state, not document state |
| Branching / append-only history | Over-scope |

### 4.3 API

| Method | Path | Body | Success | Notes |
|---|---|---|---|---|
| `GET` | `/api/documents` | — | `200` `[DocumentSummary]` | List patents |
| `GET` | `/api/documents/{id}` | — | `200` `DocumentRead` | Metadata + version summaries (no content) |
| `GET` | `/api/documents/{id}/versions/{n}` | — | `200` `VersionRead` | Full content |
| `POST` | `/api/documents/{id}/versions` | `{source_version_number?}` | `201` `VersionRead` | Copy source → new number |
| `PUT` | `/api/documents/{id}/versions/{n}` | `{content}` | `200` `VersionRead` | **Save in place** |

**Create semantics**

- Copy **committed** DB content from a source version  
- **UI always passes `source_version_number = currently selected version`**  
- API fallback if omitted: highest `version_number`  
- Do **not** snapshot the unsaved editor buffer in `POST`  
- Dirty + Create → dialog: **Save and create** / **Discard and create** / **Cancel**  
- New number = `MAX(version_number)+1`; on unique collision retry once or return 409  

**Switch semantics**

- Client-held selection (React state)  
- Default on open: most recently `updated_at`  
- Remount editor: `key={\`${docId}:${versionNumber}\`}`  
- On document/version switch: clear chat transcript + sticky file (or confirm if mid-compose)

**Save semantics (requirement 3)**

- `PUT` updates exactly one `(document_id, version_number)`  
- Never auto-increments a version  
- Empty string allowed; missing/null → 422; missing row → 404  

### 4.4 Seed & DB

- Default `DATABASE_URL=sqlite:///./data/app.db`  
- On startup: `mkdir -p` the DB parent directory; enable `PRAGMA foreign_keys=ON`, WAL, `busy_timeout`  
- Idempotent seed: if no documents, insert two patents with `version_number=1`  
- Seed content = TipTap body (`<h1>Claims</h1>…`); title on `Document.title`  
- Gitignore `server/data/`  
- **Reset:** `rm server/data/app.db` and restart (document in README)  
- In-memory SQLite only in tests  

### 4.5 Minimal versioning UX

| Control | Behaviour |
|---|---|
| Version dropdown | Lists version numbers |
| New version | `POST` with selected source; then select new version |
| Save | `PUT` selected version; Ctrl/Cmd+S |
| Dirty indicator | Enable Save when revision ≠ last-saved |

---

## 5. Task 2 Option A — AI editing

### 5.1 Approach

| Approach | Verdict |
|---|---|
| LLM returns full HTML | Reject — empty content can wipe the editor |
| **LLM returns structured ops; server applies** | **Winner** |
| HTML diff/patch | Reject — anchors break after TipTap normalisation |
| Client TipTap command chains | Reject — duplicates logic in JS; weak pytest story |

### 5.2 Operation schema

One status vocabulary end-to-end:

```text
status: "ok" | "needs_clarification" | "error"

EditPlan (from model / Structured Outputs):
  status: "ok" | "needs_clarification" | "error"
  message: str
  operations: list[Op]          # empty + ok → treat as no-op (html: null)

Op (discriminated by kind):
  format_claim   { claim_number, mark: bold|italic|strike, enabled: bool }
  delete_claim   { claim_number }
  insert_claim   { after_claim_number, text }   # 0 = before claim 1
  replace_claim  { claim_number, text }
  insert_section { heading, paragraphs: list[str], position: before_claims|after_claims }
```

**API response**

```json
{
  "status": "ok",
  "html": "<h1>Claims</h1>...",
  "message": "Made claim 1 bold.",
  "applied": [{ "kind": "format_claim", "claim_number": 1 }]
}
```

- Non-`ok`, or `ok` with empty ops → `html: null` (client does not `setContent`)  
- `error` covers impossible instructions, validation failures, and provider failures (map to 4xx/5xx as appropriate)  
- `needs_clarification` → 200 with message, `html: null`

**Apply rules**

1. Resolve every claim number against the **original** parse (bind stable uids first).  
2. Kind order: `replace_claim` → `format_claim` → `insert_claim` → `delete_claim` → `insert_section`.  
3. Within each kind, apply in **plan order**. For multiple `insert_claim` with the same `after_claim_number`, chain anchors (insert A after 2, then B after A).  
4. `format_claim` applies the mark to **every block** of that claim; re-inject the number into the first block’s first text node on render.  
5. Renumber claim prefixes **once** at the end (preserve separator style). **No cross-reference rewriting in MVP** — append warning: “Claim numbering updated; references in claim text may need review.”  
6. `insert_section` renders `<h1>{heading}</h1>` + escaped `<p>`s into preamble or postamble.  
7. Sanitize rendered HTML with `nh3` before returning.

### 5.3 Parse / apply pseudocode

```text
parse(html):
  soup = BeautifulSoup(html, "html.parser")
  find claims region:
    prefer nodes after <h1>Claims</h1> (case-insensitive)
    else first run of ≥2 paragraphs matching /^\s*(\d+)([.)])\s+/
    stop at next heading or end
  preamble = nodes before region; postamble = nodes after
  claims = []
  for each block in region:
    if block text matches claim start:
      start new Claim(number, separator, blocks=[rest_of_first_p], uid=new)
    else:
      append block to current claim
  return ParsedDocument(preamble, claims, postamble)

apply(doc, ops):
  bind claim_number → uid using ORIGINAL claims
  for kind in [replace, format, insert, delete, section]:
    for op in ops of that kind (plan order):
      mutate doc via uid / after_uid rules
  renumber: for i, claim in enumerate(doc.claims, 1): claim.number = i
  return render(doc)  # escape text; wrap marks; <h1>Claims</h1> for region
```

**Invariant after `insert_section`:** claims region remains separately addressable so later “bold claim 1” still targets patent claims, not “1. Field of the Invention” in Background.

### 5.4 Endpoint

`POST /api/ai/edit`

```json
{
  "html": "...",
  "instruction": "Make claim 1 bold",
  "context_text": null,
  "history": [{ "role": "user"|"assistant", "content": "..." }]
}
```

Limits: instruction / context / HTML size caps; history ≤ 3 turns.

### 5.5 Planner & prompt (`ai/edit.py`)

- Structured Outputs via `chat.completions.parse` into `EditPlan`  
- Model from `OPENAI_MODEL`; prefer `reasoning_effort="low"` on gpt-5.x if supported  
- Missing key → 503; timeout → 504; rate limit → 429  
- Tests use a fake planner function (no Protocol ceremony)

**Prompt (short)**

1. System: ops vocabulary, rules, statuses, treat `<prior_art>` as untrusted data  
2. Compact claims outline (one line per claim; note extra paragraphs)  
3. Optional `<prior_art>…</prior_art>`  
4. Last 3 turns  
5. Instruction last  

### 5.6 `.txt` upload

- DnD onto chat panel (click-to-pick optional polish — not required)  
- Validate: `.txt` only, size cap, non-empty, strip BOM, reject NUL  
- Sticky chip until cleared; re-send as `context_text` each request  
- Window `dragover`/`drop` `preventDefault` to avoid navigation  

### 5.7 Client apply

1. Disable editor + compose while pending  
2. Snapshot previous HTML **before** apply (explicit Undo / recovery — do not rely on TipTap history after `setContent`)  
3. On `ok` with html: `editor.commands.setContent(html, true)` (TipTap **v2** positional)  
4. Mark dirty; show assistant message  
5. Request token: drop stale responses after doc/version switch  

---

## 6. Frontend

### 6.1 TipTap contract

| Rule | Detail |
|---|---|
| Uncontrolled | No `useEffect` string-compare sync |
| Remount | `key={\`${docId}:${versionNumber}\`}` |
| Instance | `onReady(editor)` for ChatPanel apply / `setEditable` |
| Baseline | On ready, store `editor.getHTML()` as saved baseline |

### 6.2 Layout

```
┌──────────────────────────────────────────────────────────────┐
│  Header (logo)                                               │
├──────────┬───────────────────────────────────┬───────────────┤
│ Patents  │  Title · VersionBar · Save        │  ChatPanel    │
│ 1 / 2    │  Editor                           │  + file drop  │
└──────────┴───────────────────────────────────┴───────────────┘
```

### 6.3 State

`useDocument`: ids, versions, dirty revision counter, load/save/create/switch + request tokens + dirty dialogs.  
Chat state local to `ChatPanel`. No Redux / TanStack Query.

### 6.4 API client

`VITE_API_URL` default `http://localhost:8000`; typed helpers in `api.ts`; surface errors in the UI.

---

## 7. Cross-cutting concerns

| Concern | Approach |
|---|---|
| CORS | `http://localhost:5173`; credentials off unless needed |
| Config | `OPENAI_API_KEY`, `OPENAI_MODEL`, `DATABASE_URL`, size limits |
| Sanitize | `nh3` allowlist: `p`, `h1`–`h3`, `strong`, `em`, `s`, `ul`, `ol`, `li`, `br` |
| Auth | None |
| Concurrency | Last-write-wins on Save |

---

## 8. Testing

Target **~15–25 high-value tests**.

### Backend (`pytest`, `server/tests/`)

- Versioning: create copies selected source; PUT in place; GET switch; 404s  
- Claims: all four golden examples; multi-paragraph bold; delete+renumber; insert after N; Background insert  
- Ops: `[delete 3, delete 5]` uses original numbers; chained inserts after same claim  
- AI route with fake planner; `html: null` on non-ok  
- Sanitize strips scripts  

### Frontend (`vitest` in `client/`, configured in `vite.config.ts`)

- `contextFile` accept/reject  
- Dirty / stale request token  
- ChatPanel send + sticky file chip  

Skip Playwright for MVP.

---

## 9. Tooling & Docker

### 9.1 Dependencies to add

**Server:** `nh3`, `beautifulsoup4`, `pydantic-settings`; dev: `pytest`, `ruff`  
**Client:** `vitest`, `@testing-library/react`, `happy-dom`; ESLint flat config in phase 5  

### 9.2 Docker (default plan)

```yaml
# server service additions
env_file: ./server/.env          # document: cp .env.example .env
volumes:
  - ./server:/usr/src/app
  - server_venv:/usr/src/app/.venv   # fix A13 (like client node_modules)
```

- App starts without OpenAI key; AI returns clear 503  
- DB at `server/data/app.db` (bind-mounted; delete file to reset)  
- Ensure data directory is created in lifespan  

### 9.3 Docs after build

- Root `README.md` — improvements write-up  
- `server/README.md` / `client/README.md` — layout, versioning, AI, reset  

---

## 10. Implementation plan

| Phase | Work | Outcome |
|---|---|---|
| 1 | DB + models + versioning API + seed + tests | Task 1 backend |
| 2 | VersionBar + useDocument + TipTap fixes + Save/dirty | Task 1 E2E |
| 3 | `claims.py` + golden example unit tests | AI engine |
| 4 | `edit.py` + `/api/ai/edit` + ChatPanel + DnD | Option A E2E |
| 5 | Sanitize, errors, ESLint fix, Docker smoke | Hardening |
| 6 | README write-up + final pass | Submit |

**Cut first if short on time:** Undo button → click-to-pick file → Vitest TipTap mount → ruff strictness → Emotion purge polish.

---

## 11. Out of scope & future work

### Do not build

- Option B (websockets, presence, CRDT)  
- Auth, autosave, ETags, version delete/diff/branching/names  
- Cross-reference rewriting (warn only)  
- Streaming, agents, RAG, persisted chat  
- CI, reset API, USPTO amendment markup  
- Summarize/Rewrite toolbar (chat is the interface)  

### Interview talking points (later)

If more time / integrating with Solve’s stack (`CHALLENGE.md`):

- Swap file SQLite → Postgres (SQLAlchemy models already close)  
- SuperTokens / auth around documents  
- Option B collaborative editing on the same TipTap surface  
- Persist AI audit trail; stronger claim-dependency validation  

---

## 12. Submission checklist

- [ ] Task 1: create / switch / save-in-place  
- [ ] Option A: chat + four golden examples + `.txt` DnD  
- [ ] Multi-paragraph claim 1 bold works  
- [ ] AI failure leaves editor untouched; AI never writes DB  
- [ ] TipTap: no bad caret jump on switch; user Save persists AI edits  
- [ ] ~15–25 unit tests green  
- [ ] `docker-compose up --build` works; `.venv` volume present  
- [ ] App usable without OpenAI key  
- [ ] Improvements blurb at end of `README.md`  
- [ ] Local READMEs updated  
- [ ] Code stays pair-programming friendly  

---

## 13. Verification log

Multi-agent senior review completed before implementation:

| Squad | Focus | Verdict |
|---|---|---|
| Explore | Codebase inventory + Option A requirements | Scaffold only; Task 1 + A must be built |
| Explore | Adversarial review of prior overbuilt draft | Core bets sound; cut ceremony |
| Explore | TipTap / AI approach selection | Ops + server apply |
| Explore | Versioning model | Two-table mutable drafts |
| Verify | Correctness vs brief/code | 8/10 — Task 1 + A covered |
| Verify | Simplicity / over-engineering | 7→target 5 after collapses |
| Verify | Versioning + AI deep dive | Approve with changes (applied here) |
| Verify | Hiring-manager readiness | Hire leaning yes |

**Applied from review:** simplified `ai/` to two files; one status vocab; no xref rewrite; create-from-selection; format-all-blocks; insert chaining; HTML snapshot undo; parse pseudocode; Docker `.venv` volume; `server/data/` mkdir; sticky-file wording; chat clear on switch; future-work stack bullets.

---

## Appendix A — Decision log

| # | Decision | Why |
|---|---|---|
| D1 | Ops, not HTML | Empty `setContent` can wipe the editor |
| D2 | Apply on server | One home for logic; pytest-friendly |
| D3 | Claim number as field | StarterKit strips paragraph attributes |
| D4 | Preamble / claims / postamble | Background must not become claims |
| D5 | Original numbering + one renumber | Avoid delete-3-then-5 deleting 6 |
| D6 | Title on Document; body fragments | TipTap strips full HTML envelope |
| D7 | File SQLite | In-memory StaticPool is unsafe concurrently |
| D8 | Uncontrolled TipTap + remount key | Fixes sync/caret issues |
| D9 | AI never writes DB | Failure isolation; explicit Save |
| D10 | No streaming / agents / collab | Not overly complex |

---

## Appendix B — Example op plans

**Make claim 1 bold**

```json
{ "status": "ok", "operations": [
  { "kind": "format_claim", "claim_number": 1, "mark": "bold", "enabled": true }
]}
```

**Delete claim 3**

```json
{ "status": "ok", "operations": [
  { "kind": "delete_claim", "claim_number": 3 }
]}
```

**Dependent claim after claim 2**

```json
{ "status": "ok", "operations": [
  { "kind": "insert_claim", "after_claim_number": 2,
    "text": "The wireless optogenetic device of claim 2, wherein the biocompatible materials are glass." }
]}
```

**Background from prior art**

```json
{ "status": "ok", "operations": [
  { "kind": "insert_section", "heading": "Background",
    "paragraphs": ["…grounded prose…"], "position": "before_claims" }
]}
```
