# Design

**Project:** Solve Intelligence engineering challenge — patent reviewing application
**Scope:** Task 1 (document versioning) + Task 2 **Option A** (AI-powered document editing)
**Principle:** The LLM does *language*. Python does *structure*. Everything numeric or
irreversible is deterministic and unit-tested.

---

## 1. Requirements

From `README.md` — the authoritative task list.

**Global**

- Production-quality, readable code; unit tests; no bugs
- `docker-compose up --build` works
- Improvements write-up at the end of `README.md`

**Task 1 — Versioning**

1. Create new versions
2. Switch between existing versions
3. Edit **any** existing version and save **without creating a new version**

**Task 2 Option A — AI editing**

1. Chat-style UI for natural-language editing instructions
2. AI interprets the instruction and modifies the document HTML
3. Changes appear in the editor immediately
4. Drag-and-drop `.txt` upload for additional AI context

**The four example instructions are the acceptance tests.** Each probes a distinct capability:

| Instruction | Capability |
|---|---|
| "Make claim 1 bold" | Format **every** block of a multi-paragraph claim |
| "Delete claim 3" | Structural delete + renumber + cross-reference remap |
| "Add a new dependent claim after claim 2 that specifies the material is glass" | Insert + dependent phrasing + renumber |
| "Write a background section based on the prior art file I have uploaded" | Grounded generation into a Claims-only document |

`CHALLENGE.md` adds two constraints that shape *how* we build: submissions are **stress-tested
across a range of inputs**, and the next round is **pair programming without AI**. Both argue for
clarity over cleverness and for graceful degradation over feature count.

---

## 2. Inherited codebase

A working junior mock-up: load one of two patents into TipTap, edit, save. No versioning, no AI,
no tests; `openai` is declared but never imported.

Confirmed defects, and how the design answers them:

| Where | Issue | Response |
|---|---|---|
| `db.py` | `:memory:` + `StaticPool` — one shared connection, data lost on restart | File-backed SQLite via `DATABASE_URL`; `StaticPool` kept for in-memory URLs, where it is correct (see §4.4) |
| `__main__.py` seed | Hardcoded `id=1,2` inserted every startup | Idempotent seed |
| `GET /document/{id}` | Returns `None` for missing id → 500 | Explicit 404 |
| `POST /save/{id}` | Returns 200 after updating 0 rows | Rowcount check → 404 |
| `data.py` | Seed is a full HTML document; `getHTML()` drops the envelope on first save | Store TipTap body fragments; title on `Document.title` |
| `App.tsx` | `currentDocumentId` starts at `0`; saves to `/save/0` succeed silently | Store holds a real selection or none |
| `Editor.tsx` | Controlled sync effect vs. `getHTML()` → spurious `setContent`, caret reset | Uncontrolled editor, remounted by `key` |
| `Editor.tsx` | `setContent(html)` defaults to `emitUpdate=false` (TipTap v2) | Pass `true` when applying AI output |
| `App.tsx` | Errors only `console.error` | Visible error state |
| ESLint | v9 with legacy `.eslintrc.cjs` and removed `--ext` flag | Flat `eslint.config.js` |
| `docker-compose.yml` | Bind-mount masks the image `.venv`; requires an absent `.env` | Named volume for `.venv`; document `cp .env.example .env` |

**Seed facts that drive the AI design:**

- Patent 1: 8 claims, claim 1 spans **5** `<p>` tags. Patent 2: 9 claims.
- Only a **Claims** section exists — a Background must be *created*, not edited.
- Claim numbers are plain **text prefixes** (`1. `), not attributes. TipTap StarterKit's `Paragraph`
  declares no attributes, so `data-*` markers would be stripped on the round-trip.
- Patent 1 claim 7 references "claim 5" where it means claim 6 — the seed itself has a
  cross-reference bug, which is a useful reminder of why remapping matters.

---

## 3. Architecture

> **Status: both halves are built and shipping.** This note used to say the AI half was "designed,
> not implemented". It is implemented, and where the shipped code diverges from what this document
> originally committed to, the divergence is called out inline rather than silently rewritten —
> `PLAN.md` §1 and §2 record every decision, and §27.4 records what the Step 6 stress pass
> overturned. The one structural surprise: `ai/planner.py` never existed. It became eleven modules
> under `ai/`, for the reason in PLAN §1.2 — one file holding model + parse + render + six
> operations + bind + apply + renumber + remap + verify is 600+ lines and eight concerns, and it is
> precisely the file an interview centres on and the one you could not walk in sixty seconds.

```
Browser
  DocumentList · VersionBar · Editor (TipTap) · ChatPanel (+ .txt drop)
                          │
                    useDocumentStore (zustand)
                          │  api.ts
                          ▼  HTTP JSON
Server
  routers/documents.py ── crud ── models ── SQLite (file)
  routers/ai.py ── ai/graph.py (LangGraph) ── ai/nodes.py ── ai/llm.py (the only OpenAI import)
                └─ ai/apply.py ── ai/{document,operations,verify}.py
                     └─ sanitize (nh3)
```

**Save path:** editor HTML → `PUT /api/documents/{id}/versions/{n}` → sanitize → update row.

**AI path — two routes, not one.** The single `POST /api/ai/edit` this document originally
specified was superseded (PLAN §1.4): generated prose must be confirmed *before* it lands, and
human-in-the-loop lives **between two graph runs** rather than inside one.

- **Run 1 — `POST /api/ai/chat`:** editor HTML + instruction + optional file text + a `consented`
  boolean → understand → (retrieve → draft ⇄ judge | plan_ops | answer) → verify. Returns one of
  six statuses. On an unconsented version a document-changing plan comes back as a **proposal**
  carrying operations, never HTML.
- **Run 2 — `POST /api/ai/apply`:** the proposal plus the current HTML → re-validate → apply →
  verify → sanitized HTML. Deterministic and offline: it never reaches OpenAI, so it works with no
  API key configured.

**Neither route writes the database**, and the mechanism is not discipline: **neither handler takes
a `db` parameter.** Persistence is always an explicit user action — the client orchestrates version
creation through the existing `POST /api/documents/{id}/versions`.

### Layout

```
server/app/
├── __main__.py      # re-exports app so `uvicorn app.__main__:app` still works
├── main.py          # FastAPI, CORS, lifespan, routers
├── config.py        # Settings (pydantic-settings)
├── db.py            # engine, session, pragmas
├── models.py        # Document, DocumentVersion
├── schemas.py       # API models + EditPlan / Op
├── crud.py          # queries
├── sanitize.py      # nh3 allowlist
├── data.py          # seed fragments + titles
├── routers/{documents,ai}.py
└── ai/            # the engine — see server/README.md for the reading order
    ├── document.py    # ParsedDocument, parse(), render() — the round-trip contract
    ├── outline.py     # build_outline / build_context / claims_excerpt
    ├── operations.py  # the six operations, and KIND_ORDER
    ├── schemas.py     # every model↔engine contract, and require()
    ├── apply.py       # bind → apply → renumber once → remap cross-references
    ├── verify.py      # the deterministic gate on the produced artefact
    ├── understand.py  # the fast path and gate_understanding
    ├── summary.py     # one human sentence per operation
    ├── prompts.py     # prompt text only
    ├── llm.py         # THE ONLY module that imports `openai`
    ├── nodes.py       # the seven node functions and node_guard
    └── graph.py       # THE ONLY module that imports `langgraph`
server/tests/

client/src/
├── App.tsx
├── api.ts, types.ts
├── store.ts                 # useDocumentStore
├── contextFile.ts           # .txt validation
├── ai/{claims,selection,highlight,format}.ts   # claim spans, selection, the format fast-path
└── components/
    ├── Banner.tsx           # the app bar: what is open, and the two save buttons
    ├── PatentTree.tsx       # patents, with the open one expanded to its versions
    ├── Editor.tsx           # TipTap, uncontrolled, remounted by key
    ├── {Toolbar,TxtDropZone,SidePanel,Pager,TreeRow,InlineRename}.tsx
    ├── {Modal,DirtyDialog,NewPatentDialog,Spinner,Timestamp}.tsx, time.ts
    └── chat/{ChatPanel,MessageList,Message,Composer,ContextChips,ProposalPrompt}.tsx
```

**The eight engine modules — `document`, `outline`, `operations`, `apply`, `verify`, `schemas`,
`understand`, `summary` — plus `nodes.py` import neither `openai` nor `langgraph`**, so the entire
edit engine is testable with no key and no network. `T5` enforces this by glob in a fresh
subprocess, which means a new file under `app/ai/` is covered the moment it is added.

---

## 4. Task 1 — Versioning

### 4.1 Model

```
Document          id · title · created_at · versions[]
DocumentVersion   id · document_id · version_number · content · created_at · updated_at
                  UNIQUE (document_id, version_number)
```

`DocumentVersion` is a **mutable draft**, not an immutable snapshot. Saving updates the row in
place. This is the only model that satisfies requirement 3 directly.

**Invariants:** every document has ≥1 version; content lives *only* on `DocumentVersion`;
the title lives *only* on `Document`.

Rejected: a single table (conflates document identity with draft), live `Document.content` plus
history snapshots (breaks requirement 3), server-held "current version" (that's viewer state, not
document state), branching or append-only history (out of scope).

### 4.2 API

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/api/documents` | — | Document summaries |
| `GET` | `/api/documents/{id}` | — | Metadata + version list (no content) |
| `GET` | `/api/documents/{id}/versions/{n}` | — | Full version content |
| `POST` | `/api/documents/{id}/versions` | `{content}` | `201` — new version `MAX+1` |
| `PUT` | `/api/documents/{id}/versions/{n}` | `{content}` | `200` — updated in place |

The old `/document/{id}` and `/save/{id}` routes are replaced.

### 4.3 The two save buttons

Both buttons send **the current editor HTML**. That symmetry is the whole design:

| Button | Call | Effect |
|---|---|---|
| **Save** | `PUT .../versions/{selected}` | Overwrites the selected version. Never creates one. (Requirement 3) |
| **Save as new version** | `POST .../versions` | Creates version `MAX+1` from the editor buffer, leaves every existing version untouched, then selects the new one. (Requirement 1) |

Because "Save as new version" captures the live buffer rather than committed DB content, there is
no ambiguity about unsaved edits and **no dirty-state dialog is needed** when creating a version.

**Switching** (requirement 2) is client state. Switching away while dirty prompts
Save / Save as new version / Discard / Cancel — the only place a dirty guard is required.
The editor and chat panel are both remounted via `key={docId}:{versionNumber}`.

**Save semantics:** `PUT` targets exactly one `(document_id, version_number)`; empty content is
allowed; a missing row is 404; a missing/null `content` field is 422.

### 4.4 Database

- `DATABASE_URL` defaults to `sqlite:///./data/app.db`; tests use `:memory:`
- The engine builder creates the data directory and sets `foreign_keys=ON` (which is what makes
  `ondelete="CASCADE"` real) and `busy_timeout=5000` (two tabs saving at once wait rather than
  fail). Journal mode is left at SQLite's default; WAL would be an easy addition but nothing in
  this app's read/write pattern currently needs it
- `StaticPool` is still used, but **only** for in-memory URLs: a shared in-memory database has no
  file to share, so each pooled connection would otherwise see its own empty database. The
  inherited bug was `:memory:` as the *production* URL; the pooling workaround it required remains
  correct for tests
- Seed is idempotent: if no documents exist, insert two patents at `version_number=1`
- Reset by deleting `server/data/app.db` (documented in the README); `server/data/` is gitignored

---

## 5. Task 2 Option A — AI editing

> **Status: shipped.** This section was written as forward design and is kept in that voice; where
> the shipped code diverges from it, the divergence is called out inline rather than silently
> rewritten. `PLAN.md` §1 and §2 record every such decision.

### 5.1 Why structured operations, not HTML

The LLM returns a validated JSON plan; deterministic Python applies it. Reasons, in order of
weight:

1. **Renumbering must be deterministic.** Claim numbering is a correctness property of a patent,
   not a stylistic one. An LLM re-emitting 8 claims will eventually miscount.
2. **The engine is testable without an API key.** Every apply rule is a pure function over a
   parsed document — the bulk of the test suite needs no network.
3. **Blast radius is bounded.** The model cannot silently alter a claim it wasn't asked to touch,
   because it never emits the document.
4. A truncated or empty completion can't wipe the editor.

Rejected: full-HTML rewriting (all four points fail), HTML diff/patch (anchors break under TipTap
normalisation), client-side TipTap command chains (duplicates logic in JS, weak test story).

### 5.2 Document model

```
ParsedDocument
├── preamble  : Block[]    # anything before the claims (e.g. an inserted Background)
├── claims    : Claim[]    # uid · number · separator · blocks[]
└── postamble : Block[]    # anything after
```

A claim owns **all** of its paragraphs — that is what makes "make claim 1 bold" bold all five
blocks rather than the first line. The number is a **field**, stripped on parse and re-injected on
render, so renumbering never touches user text. The three-region split guarantees an inserted
"Background" section can never be parsed as a claim.

**Parse:** find the claims region (paragraphs after an `<h1>Claims</h1>`, else the first run of ≥2
paragraphs matching `^\s*(\d+)([.)])\s+`), stop at the next heading. A paragraph matching the claim
prefix starts a new claim; every other paragraph appends to the current one. Whitespace is
normalised on parse: the seed HTML is pretty-printed, while `getHTML()` output is collapsed to a
single line — the seed is stored pre-normalised so both paths present the same shape.

### 5.3 Operations

Designed to cover the *document model*, not just the four examples — so off-script instructions
still land somewhere:

```
format_claim   (claim_number, mark: bold|italic|strike, enabled)
delete_claim   (claim_number)
insert_claim   (after_claim_number, text)          # 0 = before claim 1
replace_claim  (claim_number, text)                # rewrite / shorten / amend
insert_section (heading, paragraphs[], position: before_claims|after_claims)
replace_text   (find, replace)                     # document-wide
```

**Six operations, not the seven this document first listed.** Two cuts, both in PLAN §1.1:

- **`delete_section` was removed.** No requirement asked for it, and its failure mode is
  *destroying the patent* — which is why it needed a "refuses to delete the Claims heading" guard,
  a guard that existed only because the operation existed. It also owned one of three ordering
  rules. *"Rewrite the background"* now returns `needs_clarification`, which is this document's own
  stated principle rather than an exception to it.
- **`replace_text` lost its `scope` field.** Nobody asked for claim-scoped find/replace, and it
  bought a `"claim:N"` string-parsing failure mode. Replacement is document-wide, and when a phrase
  occurs more than once the operation says so: *"That text appears 8 times; all of them were
  changed."*

Anything outside this vocabulary returns `needs_clarification` with a message explaining what the
assistant *can* do. **An unsupported instruction never produces a partial or wrong edit** — that
is the behaviour that survives stress testing.

### 5.4 Apply pipeline

```
HTML → parse → bind claim numbers to uids (against the ORIGINAL parse)
     → apply ops in fixed kind order
     → renumber claims 1..n
     → remap in-text cross-references via the old→new map
     → render → sanitize → return
```

Rules:

1. **All claim references resolve against the original parse.** `[delete 3, delete 5]` deletes the
   claims the user saw, not claim 6.
2. Fixed kind order: `replace_text` → `replace_claim` → `format_claim` → `insert_claim` →
   `delete_claim` → `insert_section`. Within a kind, plan order (Python's sort is stable). Multiple
   inserts after the same claim chain off each other. Three of these adjacencies are *necessary*
   and one is a *choice*; the code says which, because it is the most likely "why?" in a pairing
   round, and reversing the tuple must fail a test.
3. `format_claim` applies the mark to **every block** of the claim.
4. Renumber **once**, at the end. Then rewrite `claim (\d+)` references through the old→new map.
   References to a *deleted* claim are **not** rewritten — they are reported as a warning, because
   guessing the author's intent there would be worse than flagging it.
5. Text is escaped on render; only the tags we generate can be emitted.

### 5.5 Endpoint

**Run 1 — `POST /api/ai/chat`**

```json
{ "document_id": 1, "version_number": 2, "html": "...", "instruction": "Make claim 1 bold",
  "context_text": null, "context_name": null, "selection": null, "consented": false,
  "pending_question": null, "clarify_count": 0,
  "history": [{"role": "user", "content": "..."}] }
```

```json
{ "status": "applied" | "proposal" | "answer" | "no_change" | "needs_clarification" | "error",
  "html": "<h1>Claims</h1>...",
  "message": "Made claim 1 bold.",
  "proposal": { "proposal_id": "...", "base_sha256": "...", "operations": [...] },
  "citations": [3], "options": ["Make claim 1 bold."],
  "warnings": ["Claim 4 references claim 3, which was deleted."] }
```

**Six statuses, not three** — the wire shape grew with the design. `html` is non-null **iff**
`status == "applied"`, and that is not a convention: a `model_validator` on the response model
enforces it, because the client's dirty flag rests on `html != null ⟺ the document changed`. It is
therefore also null when operations ran and changed nothing. **The client calls `setContent` only
when `html` is non-null, so a failed or ambiguous AI request leaves the document byte-identical.**

**Run 2 — `POST /api/ai/apply`** takes `{ html, proposal }` and returns
`{ status, html, message, verification, warnings }`. It re-hashes the submitted HTML against
`proposal.base_sha256` and **409s on a mismatch**, so an "Apply" cannot write an edit computed
against text the user has since changed. A TTL on `created_at` gives staleness detection with
**zero server state** — there is no proposal store to expire, restart or garbage-collect.

Size caps on instruction, context, selection and HTML; history capped at 3 turns (= 6 messages).
Missing or placeholder key → **503**, rejected key → **502**, timeout → **504**, provider rate
limit → **429** — each with a sentence a user can read and act on.

### 5.6 Planner

**`ai/llm.py` is the only file that imports `openai`** (this section said `ai/planner.py`; see the
status note in §3 — the planner became a package). `ai/graph.py` is the only file that imports
`langgraph`. Structured Outputs validate every model response into Pydantic models before it
reaches the apply layer, and `require()` re-validates each operation before it reaches the applier.

The prompt carries: the operation vocabulary and rules; a compact claims outline (one line per
claim, noting extra paragraphs) rather than the full HTML; **the full text of the claims actually
being edited** — added after the live pre-flight, which showed a model correctly *refusing* to
rewrite a claim it had only seen truncated to 240 characters; the optional prior-art text; the last
3 turns; the instruction last.

**Uploaded file text is data, never instructions.** It is delimited and the system prompt states
that content inside the delimiter is reference material only. Tests inject a fake planner
function — no Protocol, no DI container.

### 5.7 `.txt` upload

Dropped onto the chat panel. Validated client-side: `.txt` only, non-empty, size-capped, BOM
stripped, NUL rejected. Shown as a chip that stays until cleared and is re-sent with each request.
The server never stores it. Window-level `dragover`/`drop` handlers prevent the browser from
navigating away on a stray drop.

---

## 6. Frontend

### 6.1 State — one store, deliberately scoped

`useDocumentStore` (zustand) holds what more than one component needs: document list, selected
document and version, version list, dirty flag, the TipTap editor instance, and load/save/create
actions.

The editor instance is the reason a store beats prop-drilling: `ChatPanel` must apply AI output to
the editor and mark the document dirty, and it sits in a different branch of the tree. Without a
store that becomes a callback chain through `App`.

**Chat messages and the attached file stay local to `ChatPanel`** — nothing else reads them. The
rule is "in the store only if two components need it," which keeps the store small enough to read
in one screen.

### 6.2 TipTap contract

- **Uncontrolled.** No effect comparing HTML strings against `getHTML()`.
- **Remount on switch:** `key={docId}:{versionNumber}` on both `Editor` and `ChatPanel` — this
  loads the right content, resets the caret cleanly, and clears the chat, all with one mechanism.
- On ready, register the instance in the store; on unmount, clear it — identity-guarded, because
  React can commit the next key's `onCreate` before the previous child's cleanup runs.
- **Dirty is a one-way boolean, not a diff against a baseline.** `Editor.onUpdate` is the only
  writer; the two save actions and the two selection actions are the only clearers. No saved-HTML
  baseline is recorded anywhere. Comparing strings would buy "typed and undid it" detection at the
  cost of a second source of truth and a race with TipTap's async normalisation — not worth it,
  and one writer to one flag is the version that can be explained live.
- AI apply uses `setContent(html, true)` (TipTap v2 positional `emitUpdate`) so the dirty flag
  fires through that same single writer.
- Native `Cmd+Z` is verified as the undo path for AI edits before any custom Undo is considered.

### 6.3 Layout

```
┌──────────┬────────────────────────────────────┬──────────────┐
│ Patents  │ Title · Version ▾ · Save · Save as │ Chat         │
│          │ TipTap editor                      │ + .txt drop  │
└──────────┴────────────────────────────────────┴──────────────┘
```

Every request carries a monotonic token, so a slow response for one document/version can never
land in another. All errors render in the UI, never only in the console.

---

## 7. Cross-cutting

| Concern | Approach |
|---|---|
| CORS | `http://localhost:5173` |
| Config | `OPENAI_API_KEY`, `OPENAI_MODEL`, `DATABASE_URL`, size limits — via `pydantic-settings` |
| Sanitising | `nh3` allowlist on the **save** path, where content is client-supplied. The list is derived from what TipTap's StarterKit can render, not from a generic "safe HTML" list — stripping a tag StarterKit supports is data loss wearing a security costume: `p`, `h1`–`h6`, `ul`, `ol`, `li`, `blockquote`, `pre`, `code`, `br`, `hr`, `strong`, `em`, `s`, plus `start`/`type` on `ol`. Known cosmetic loss: `code[class="language-*"]`. **`TECHNOLOGY.md` §2.2 is the source of truth for this list** — one allowlist, stated once, so the two documents cannot drift |
| No API key | Versioning and editing work fully; the chat panel shows a clear "AI unavailable" state |
| Auth | None (out of scope) |
| Concurrency | Last-write-wins on save |

---

## 8. Testing

Chosen for value rather than coverage percentage.

> **Status: every row below is written and passing.** The count outgrew the "roughly 20" this
> section originally planned for — 708 in total, 496 backend and 212 frontend — because the AI
> layer arrived with four deterministic gates of its own. `PLAN.md` §31.2 is the single source of
> truth for the number. What holds regardless of the count: **none of them requires an API key.**

**Backend (`pytest`)**

- **Round-trip stability:** parse → render → parse is lossless on both seed patents *(the safety
  net every other AI test depends on)*
- The four README examples, end to end through the apply layer
- Multi-paragraph claim 1 formats all five blocks
- `[delete 3, delete 5]` resolves against original numbering; chained inserts after one claim
- Cross-reference remap after delete, including the dangling-reference warning
- Versioning: `PUT` updates in place and creates nothing; `POST` creates `MAX+1` from the posted
  content and leaves other versions untouched; 404s
- AI route with a fake planner: non-`ok` returns `html: null`
- Sanitiser strips `<script>`

**Frontend (`vitest`)**

- `.txt` validation accepts/rejects correctly
- Store: dirty flag, stale-response rejection
- ChatPanel: send flow, sticky file chip

---

## 9. Build order

| Phase | Work | Done when |
|---|---|---|
| 0 | `.env`, compose fixes, seed normalisation, file DB, ESLint flat config | `docker-compose up --build` is green |
| 1 | Models, versioning API, idempotent seed, tests | Task 1 backend, tested |
| 2 | Store, version bar, two save buttons, TipTap fixes | **Task 1 fully demoable** |
| 3 | `ai/document.py` + full engine test suite | Engine correct with no LLM involved |
| 4 | `ai/` engine, graph, `/api/ai/chat` + `/api/ai/apply`, ChatPanel, drag-and-drop | **Option A demoable** |
| 5 | Error states, no-key mode, sanitising, stress pass | Hardening |
| 6 | README write-up, final review | Submit |

**Verified before implementation** (by running the real libraries — see `CLAUDE.md`):

- TipTap round-trip is byte-stable on the seed, so the parser has one canonical input shape.
- `getHTML()` emits collapsed single-line body HTML and drops `<head>`/`<title>` — so the seed is
  stored pre-normalised in that exact form, and the title moves to `Document.title`.
- `openai` 1.109.1 exposes `client.chat.completions.parse` on the stable path; the `beta.chat`
  module no longer exists.
- TipTap 2.27.1 `setContent` takes `emitUpdate` **positionally**.

---

## 10. Out of scope

**Not building:** Option B (websockets, presence, CRDT); auth; autosave; version delete, diff or
branching; streaming responses; agent loops or RAG; persisted chat history; CI; USPTO amendment
markup. *(Version **rename** was on this list and then shipped in Task 1 — it is in the UI, and
leaving it here would teach the reader to distrust the rest of the list.)*

**Known limitations, accepted deliberately for this submission:**

- **No migrations.** Schema creation is `Base.metadata.create_all`, which creates missing tables
  and never alters an existing one — change a column and an already-seeded `app.db` silently keeps
  the old shape. Fine while the reset story is `rm server/data/app.db`; the first thing to add on
  the way to a real deployment is Postgres + Alembic.
- **Naive UTC timestamps at 1-second resolution.** `created_at`/`updated_at` use SQLite's
  `CURRENT_TIMESTAMP` via `func.now()`, so they carry no timezone and two saves inside the same
  second are indistinguishable. `DateTime(timezone=True)` with a database-side default is the
  correct fix; it is a schema change, so it belongs in its own change alongside migrations.
- **Single-viewport layout by choice.** The three-column grid is sized for a desktop review
  session and does not reflow; at narrow widths the columns compress rather than stack. Responsive
  behaviour is a real gap, not a subtle one — it is simply not what this submission is
  demonstrating.
- **TipTap StarterKit drops tables, links and images.** Anything outside the StarterKit schema is
  discarded on paste, and the save-path sanitiser allowlist matches that schema deliberately. For a
  patent tool this matters more than it sounds: attorneys paste claim sets and prior-art passages
  out of Word, and a pasted table disappears without a warning. Adding the `Table`, `Link` and
  `Image` extensions (and widening the allowlist in step) is the fix.

**Would do next, given more time** — and how it maps onto Solve's stack:

- SQLite → **Postgres on RDS**; the SQLAlchemy models port with a URL change
- **SuperTokens** for auth, scoping documents to users
- Option B collaborative editing on the same TipTap surface (Yjs)
- Persisted AI audit trail: store each plan alongside the version it produced
- Claim-dependency validation: detect circular or forward dependencies, not just renumber

---

## Appendix — Decision log

| # | Decision | Why |
|---|---|---|
| D1 | LLM emits ops, not HTML | Deterministic numbering; testable without a key; bounded blast radius |
| D2 | Model-complete op set + `needs_clarification` | Survives stress testing; never a partial or wrong edit |
| D3 | `DocumentVersion` is a mutable draft | The only model that satisfies requirement 3 |
| D4 | Claim number as a field, re-injected on render | StarterKit strips paragraph attributes; text is never the source of identity |
| D5 | Renumber once, then remap cross-references | Correct patents after a delete; dangling refs flagged, not guessed |
| D6 | preamble / claims / postamble | A Background can never be parsed as a claim |
| D7 | AI route never writes the DB | Failure isolation; saving stays an explicit user action |
| D8 | File-backed SQLite | Versions survive restart; fixes the shared-connection flaw |
| D9 | Uncontrolled TipTap + remount key | One mechanism fixes content load, caret reset and chat clearing |
| D10 | Both save buttons send the editor buffer | Symmetric, unambiguous, removes the create-version dirty dialog |
| D11 | Zustand for shared state only | ChatPanel needs the editor instance; chat state stays local |
| D12 | No streaming, agents or collaboration | "Not overly complex" is an explicit requirement |
