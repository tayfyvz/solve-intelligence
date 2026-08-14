# Solve Intelligence Engineering Challenge

## Objective

You have received a mock-up of a patent reviewing application from a junior colleague. It is incomplete and needs work. Your job is to extend and improve it to a standard you'd be comfortable shipping to production. This means:

- Clean code that is production quality
- Unit tests
- No bugs

After completing the tasks below, add a couple of sentences to the end of this file briefly outlining what improvements you made.

## Docker

From a clean clone, two commands:

```sh
cp server/.env.example server/.env   # then put the provided OpenAI API key in it
docker-compose up --build
```

The copy is not optional: Compose resolves `env_file` up front, so `docker-compose up` fails
with "env file … not found" if `server/.env` is missing.

Client: http://localhost:5173 · API: http://localhost:8000

If a rebuild ever picks up a stale dependency environment, clear the anonymous volumes:

```sh
docker compose down -v && docker-compose up --build
```

## Task 1: Implement Document Versioning

Currently, the user can save a document, but there is no concept of **versioning**. Paying customers have expressed an interest in this and have requested the following:

1. The ability to create new versions
2. The ability to switch between existing versions
3. The ability to make changes to any of the existing versions and save those changes (without creating a new version)

You will need to modify the database model (`app/models.py`), add some API routes (`app/__main__.py`), and update the client-side code accordingly.

## Task 2: Choose One of the Following

Complete **one** of the two options below.

### Option A: AI-Powered Document Editing

Implement a chat interface that allows users to edit the patent document using natural language instructions.

Minimal Requirements:
1. A chat-style UI panel where users can type editing instructions
2. The AI should interpret the instruction and modify the document HTML accordingly
3. Changes should be applied to the editor and visible immediately
4. Support drag-and-drop .txt file upload to the chat to provide additional context for the AI


Example instructions your solution should handle:
- "Make claim 1 bold"
- "Delete claim 3"
- "Add a new dependent claim after claim 2 that specifies the material is glass"
- "Write a background section based on the prior art file I have uploaded"

### Option B: Live Collaboration

Implement real-time collaborative editing so multiple users can work on the same document simultaneously.

Minimal Requirements:
1. Multiple users should be able to view and edit the same document at the same time
2. Changes made by one user should appear in real-time for all other users
3. Show presence indicators (e.g., cursors, user avatars) to indicate where other users are editing
4. Handle conflict resolution gracefully when multiple users edit the same section

## Note
You may use AI (and the API key we have provided) to assist with coding on this task. When we review submissions we will stress test your solution across a range of inputs and common user behaviours, so do consider this when designing your solution. 

If your submission passes our review, the next stage will involve pair programming without AI assistance.

Good luck!

## Improvements made

**Both tasks are implemented.** Task 1 is document versioning. Task 2 is Option A: AI-powered
editing, where the model emits **structured operations** and Python applies them — it never writes
the document's HTML. **784 tests pass (558 backend, 226 frontend), and none of them needs an API
key.**

The rest of this section is the reasoning, because the interesting parts of this submission are
decisions rather than features. `DESIGN.md` covers what and why, `TECHNOLOGY.md` why each tool, and
`PLAN.md` the order it was built in, with every correction recorded as it was found.

---

### Task 1 — Versioning

Content moved off `Document` onto a `DocumentVersion` table. **A version is a mutable draft, not an
immutable snapshot**, and that is the only model that satisfies requirement 3 directly:

| Requirement | Mechanism |
|---|---|
| 1. Create new versions | `POST /api/documents/{id}/versions` → `MAX(version_number) + 1`, from the editor buffer |
| 2. Switch between versions | `GET …/versions/{n}`; the editor remounts on `key={docId}:{versionNumber}` |
| 3. **Edit an existing version and save without creating one** | `PUT …/versions/{n}` updates in place. **It cannot create a version** — a test asserts the count is unchanged |

An immutable-snapshot model would have made requirement 3 either impossible or a lie (silently
forking a new version on every save). Switching away from unsaved edits raises a **Save / Save as
new version / Discard / Cancel** dialog rather than choosing for the user.

### Task 2 — the AI edits operations, not HTML

The model never returns document HTML. It returns a validated plan of operations from a closed
six-item vocabulary, and deterministic Python applies them:

```
format_claim · delete_claim · insert_claim · replace_claim · insert_section · replace_text
```

**Four reasons, the first of which is the whole argument:**

1. **Claim numbering is a correctness property of a patent, not a stylistic one.** Delete claim 3
   and everything below must shift up with no gaps, and every *"of claim 4"* elsewhere in the
   document must be rewritten to match. Get it wrong and the patent has a real legal defect. That
   is arithmetic, so Python does it — renumber exactly once, at the end, then remap references with
   a single `re.sub` and a callable. (A loop of `str.replace` double-applies: deleting claim 3 gives
   the chain `4→3, 5→4, 6→5`, which cascades 6 all the way to 3. There is a regression test.)
2. **A wrong operation is visible; wrong HTML is not.** *"Delete claim 3"* is reviewable before it
   runs. A 3 KB HTML blob differing in ways nobody can diff by eye is not.
3. **It is testable without a network.** The engine is pure functions over a parsed document, so
   the great majority of the suite runs with no key. The eight engine modules import neither
   `openai` nor `langgraph`, and a test enforces that by glob in a fresh subprocess.
4. **The blast radius is bounded by the vocabulary.** The model cannot express "replace the whole
   document", because there is no operation that says it.

The pipeline is a small LangGraph state machine — `understand → retrieve → (plan_ops | draft ⇄
judge | answer) → verify`. LangGraph earns its 21 transitive packages on one point: the
`draft ⇄ judge` cycle is a genuine loop with a retry bound and accumulated critique, and that is
exactly where hand-managing state in one function stops being the obvious solution. Everything else
about it is deliberately boring — no checkpointer, no agents, no RAG, no streaming.

### Sticky per-version consent

**The first AI change on a version is confirmed by the user and creates a restore point; every
change after that on the same version is ordinary editing.** Any navigation — a different patent or
a different version — clears consent, so the next change is confirmed again. "Undo the AI" is
therefore a version switch, which is durable, rather than a hope that the undo stack is deep enough.

The arithmetic is why it is shaped this way. A seven-instruction editing session costs **seven**
versions under "version every AI edit", **one** under "never version", and **two** under this rule —
and every one of those two was created by a human clicking Proceed.

An earlier design gated on operation *kind* (generative vs mechanical). It was wrong in both
directions: it waved `delete_claim` straight through with no restore point, and it charged a new
version for each individually bolded claim.

### The four layers that make an unproven LLM acceptable in a legal document

Each is deterministic Python, and each catches a different class of failure:

| Layer | Guarantees |
|---|---|
| `gate_understanding` | **A nonexistent claim cannot reach the planner.** *"Delete claim 99"* is answered *"There is no claim 99 in this document — it has 8 claims, numbered 1 to 8"* without an LLM call, so no operation exists to partially apply |
| `require()` | **A malformed operation cannot reach the applier** — including one round-tripped back through an untrusted client, which is re-validated from scratch |
| `verify()` | **An unsound artefact cannot reach the client.** After applying, the result is re-parsed and checked: claims still numbered `1..n`, no claim emptied, the Claims heading intact. On failure the response is `html: null` and the document is byte-identical |
| **consent** | **An unreviewed first change cannot reach the database.** On an unconsented version the server never produces edited HTML at all — the proposal carries operations and a summary, never HTML |

Invariant behind all four: **the client calls `setContent` only when `html` is non-null**, and
`html` is non-null *iff* the status is `applied` — enforced by a validator on the response model,
not by convention.

### Inherited bugs found and fixed

- `POST /save/0` returned **200 after updating zero rows** — a silent no-op that looked like a save
- **500 instead of 404** on a missing document
- A caret race in the controlled-sync effect: the editor fought the user's cursor mid-typing. Fixed
  by making TipTap **uncontrolled** and remounting by key — deleting the effect rather than patching it
- **The patent title was destroyed on first save**, because `getHTML()` returns body content only.
  The title belongs on `Document.title`, never in the content column
- `allow_origins=["*"]` with `allow_credentials=True` — **invalid per the CORS spec**; browsers
  reject the pairing outright
- `npm run lint` was broken under ESLint 9 (no flat config), so it checked nothing
- The Docker bind mount **masked the image's `.venv`**, whose interpreter symlink then dangled
- The seed was re-inserted with hardcoded ids on every startup — harmless on `:memory:`, an
  `IntegrityError` on the **second** boot of a file-backed database
- **`connect_args={"check_same_thread": False}` was passed unconditionally.** It is a `sqlite3`-only
  argument and psycopg raises `TypeError` on it, so the documented "port to Postgres by changing a
  URL" claim was false. Now dialect-guarded, and the claim is true
- **A repeated AI instruction 409'd its own save**, because the auto-generated version name collided
  on the unique index. The client now retries once unnamed and the server auto-names

### Accepted limitations — decisions, not oversights

Cross-reference **ranges** (*"any of claims 1, 2 or 5"*) resolve the first number only ·
`replace_text` is document-wide and case-sensitive; when a phrase occurs many times it says so
(*"That text appears 8 times; all of them were changed"*) · `replace_text` across inline tags is
detected and warned, never edited · a single-claim document with no `<h1>Claims</h1>` parses as
no-claims (conservative by design; an edit that *reduces* a document to one claim synthesises the
heading) · `code[class="language-*"]` is stripped on save · **last-write-wins** on concurrent saves ·
renaming the Claims heading to a phrase not containing the word "claim" makes it undetectable, and
is refused with *"The edit removed the Claims heading"* · `max_retries=0`, so one transient provider
failure is one failed request with a readable message — a deliberate trade, because a single SDK
retry doubles the worst case and collapses the timeout chain.

**Prompt injection via an uploaded `.txt` is structurally bounded, not prevented.** Prompting cannot
prevent prompt injection, and this submission does not claim otherwise. What bounds it: the
`understand` node never sees the file at all, so an injected file cannot influence routing or
targeting; the model can only emit six operation kinds; `require()` and `verify()` reject malformed
and unsound results; on an unconsented version nothing reaches the editor without a human clicking
Apply; and ⌘Z reverts a bad apply in one keystroke. A successful injection costs the user one
rejected proposal. Three escalating attempts were tried by hand and refused with the document
byte-identical — which is evidence, not proof.

---

## Production readiness

The brief asks for *"a standard you'd be comfortable shipping to production"*. The honest position
is: **the application logic is production-quality; the deployment is a development compose stack.**
Both halves of that sentence need saying, because a reviewer who finds `--reload` in a CMD with no
acknowledgement concludes we did not look.

### Fixed before submission

- **Both images now default to their production shape**, and compose selects development. The
  server image's own CMD is `app.main:app` with no `--reload`, `--proxy-headers`, and `--workers 1`
  *with a comment explaining why not 4* — four processes writing one SQLite file is contention, not
  concurrency. The client Dockerfile builds a static bundle behind nginx; `docker-compose.yml` asks
  for the `dev` target.
- **Request size limits at the edge.** The application's 413s run *after* FastAPI has parsed the
  body, and uvicorn has no body limit, so `client_max_body_size 2m` in `nginx.conf` is the only
  thing that stops the bytes. Verified: a 3 MB POST is refused by nginx before FastAPI sees it.
- **CORS is uncredentialed and narrow** — there is no auth, no cookie and no `Authorization` header
  anywhere in the client, so credentialed CORS bought nothing and only widened the surface.
- **A logging policy, not just a log level.** The document being processed is a customer's
  unpublished patent application, so **a log line is a disclosure**: the document, the instruction,
  the prompt, the model's response, the selection, the uploaded file and the key are never logged,
  at any level, including in exception messages. Lengths, counts, kinds, enum values and truncated
  hashes instead — and a per-request UUID threaded from route to graph so the lines join up. There
  is no debug flag that widens this, because a flag that can be set will be set in production.
  Three tests assert it by running a real request whose every field carries a distinct secret.
- **SQLite→Postgres portability is real**, not aspirational (the `check_same_thread` fix above).

### Deliberately not built — with the named fix for each

- **No authentication, authorisation or multi-tenancy.** The largest gap by far. Every document is
  world-readable and world-writable to anyone who can reach the port. *(It is also why a
  client-supplied `consented` flag is acceptable: there is no privilege for a hostile client to
  escalate to.)* Fix: SuperTokens, and an `owner_id` scoping documents to users.
- **No rate limiting or cost controls.** `POST /api/ai/chat` is unauthenticated and spends money on
  every call. Fix: a per-IP limit at the edge plus a daily token budget in `llm.py`.
- **No schema migrations.** `create_all()` only, which silently does **nothing** when a column
  changes on a database with real rows. Fix: Alembic. The single biggest data-layer gap.
- **No backups** for `server/data/app.db`; no retention, no restore procedure.
- **Last-write-wins concurrency.** Fix: `If-Unmodified-Since` on `updated_at`, or a row revision
  counter → 412. *(The AI surface is the exception — it does have a drift guard, because a
  1.5–30 s window makes that race likely rather than theoretical.)*
- **No CI.** The tests exist and pass; nothing runs them on push.
- **`/api/health` is a liveness probe, not a readiness one** — it returns 200 while the database is
  unreachable. Fix: add `/api/ready` executing `SELECT 1`.
- **No metrics, alerting, tracing or structured logs.** One request UUID passed by hand is not
  tracing: no spans, no propagation, no sampling, no backend.
- **`asyncio.wait_for` bounds the response, not the work.** At 75 s the request returns 504 and the
  handler is released, but the worker thread is not cancellable — the OpenAI call runs to
  completion and bills in full. Nothing can be corrupted, because the graph is pure and holds no
  database handle, so the exposure is cost and capacity, not data. Fix: the async OpenAI client,
  where cancellation propagates to the socket.
- **`langsmith` arrives transitively with `langgraph`.** If `LANGSMITH_TRACING` or
  `LANGCHAIN_TRACING_V2` is ever set, prompts — i.e. patent text — go to LangChain's servers. No
  code path enables it and `.env.example` documents it, but that is a documentation-only mitigation
  and a weak one. The strong version is an egress allowlist on the container.

---

## Testing

**784 tests, and zero of them require an API key.** They target meaningful behaviour rather than a
coverage number.

- The **parse → render → parse round-trip on both seed patents** is the safety net the whole AI
  layer rests on. It was written first, and it is what makes a failing operation test mean "the
  operation is wrong" rather than "the parser is wrong".
- **The four README example instructions are acceptance tests**, and were also walked by hand in a
  browser against the real API — recorded row by row in `PHASE6-MANUAL.md`, including what that walk
  found and the rows it did **not** cover.
- The AI routes inject a fake graph; the graph tests inject fake LLM callables. Two seams, two
  levels, and neither reaches the network.
- A contract test asserts the client's TypeScript types still mirror the server's Pydantic models,
  so renaming a field on either side goes red instead of failing silently at runtime.

The stress pass found four defects that no test could have caught, because by design no test makes a
live API call. The most serious: `reasoning_effort` and `temperature != 1` turn out to be **mutually
exclusive** on this model, and the shipped configuration sent both — so three of the five nodes
returned 400 on every live call. The pre-flight had measured each parameter *independently* and
recorded both as accepted. Both measurements were correct; the combination was never tried. There is
now a test that asserts the **shipped call**, which is the assertion that was missing.

---

## Future work, mapped onto Solve's stack

- **SQLite → Postgres on RDS**, with Alembic for migrations
- **SuperTokens** for auth, scoping documents to users
- **Option B on the same TipTap surface** — Yjs gives collaborative editing without replacing the editor
- **A persisted AI audit trail**, storing each plan beside the version it produced — what a
  regulated-document product would want
- **Claim-dependency validation** — circular and forward dependencies, not just renumbering
- **Optimistic concurrency** (`If-Unmodified-Since` on `updated_at`) to replace last-write-wins
- **A discriminated-union `Op`** once the vocabulary grows past six
- **Streaming per-node progress**, replacing the fixed labelled stepper
