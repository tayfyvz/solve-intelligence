# Technology & Approach Decisions

Every technology, method and technique used in this solution, with justification, the alternatives
considered, and how each claim was verified.

`DESIGN.md` = what we build. `CLAUDE.md` = how to work in the repo. **This file = why each choice.**

---

## 0. Decision criteria

Every choice below was tested against four questions, in order:

1. **Does it fit Solve Intelligence's stack?** (`CHALLENGE.md`: TypeScript, React, Python, FastAPI,
   SQLAlchemy, Postgres, OpenAI, Docker) — a submission that looks like their codebase is worth
   more than one that shows off unfamiliar tools.
2. **Can I explain it live, without AI?** The next round is pair programming. Anything I can't
   defend in 60 seconds is a liability, not an asset.
3. **Does it earn its weight?** Every dependency is a thing to justify. Default answer is no.
4. **Does it make the solution more testable?** The brief asks for unit tests; architecture that
   resists testing is the wrong architecture.

A recurring theme: **we add almost nothing.** Four backend libraries, one frontend library, plus
test tooling. Everything else was already in the scaffold.

### 0.1 Domain primer: what is a claim?

Most of this document talks about "claims", so here is what they are in plain language.

A patent has several sections — a Background (what came before), a Description (how the invention
works), and **Claims**. The Claims section is the legally binding part: **it defines exactly what
the inventor owns.** Everything else is context; the claims are the fence around the property.

Claims are written as a numbered list, and they come in two kinds:

- **Independent claim** — stands on its own and describes the invention completely.
  *"1. A wireless optogenetic device comprising: a body holding light transducing materials…"*
- **Dependent claim** — refers to an earlier claim and narrows it.
  *"2. The wireless optogenetic device **of claim 1**, wherein the biocompatible materials are
  glass."*

Three properties of claims drive almost every design decision in this document:

1. **A claim can span several paragraphs.** In our seed data, claim 1 is **five** `<p>` tags — the
   number appears only in the first one. So "make claim 1 bold" must bold all five, which is why we
   group paragraphs into a claim object rather than treating each paragraph separately.
2. **The numbers are legally meaningful and must stay sequential.** Delete claim 3 and everything
   below must shift up. You cannot leave a gap. This is why numbering is done by deterministic
   Python and never by the LLM.
3. **Claims reference each other by number.** If claim 5 says "of claim 4" and the numbers shift,
   that sentence now points at the wrong claim — a real legal error. This is why we remap
   cross-references after renumbering.

One last quirk that shapes everything: **in this document the claim numbers are just text.** They
are typed at the start of a paragraph (`1. A wireless…`), not stored as structured data. There is
no markup saying "this is claim 3". We have to recognise them by reading the text.

---

## 1. Inherited stack — kept, and why

These were chosen by the previous developer. Keeping them is itself a decision.

| Technology | Version | Verdict | Reasoning |
|---|---|---|---|
| **React** | 18.3 | Keep | Solve's frontend. React 19 upgrade is churn with no benefit to the brief. |
| **TypeScript** | 5.6 | Keep | Explicitly in their stack. Types are how the API contract stays honest across the wire. |
| **Vite** | 5.4 | Keep | Already configured, fast, and it hosts Vitest with zero extra config. |
| **TipTap** | 2.27.1 | Keep | **Strategically important**: `CHALLENGE.md` says *"TinyMCE 6 — we don't like this"*. TipTap is the ProseMirror-based editor teams migrate to. Also it's the scaffold's choice, and replacing the editor is not the assignment. |
| **Tailwind** | 4.1 | Keep | Already wired via `@tailwindcss/vite`. Ripping it out to hand-write CSS would be pure cost. |
| **axios** | 1.7 | Keep | Already used. `fetch` would be one fewer dependency, but rewriting working code to save 13KB is not a real improvement. |
| **FastAPI** | 0.115 | Keep | Solve's backend framework. Pydantic-native validation is doing real work for us at the AI boundary. |
| **SQLAlchemy** | 2.0.45 | Keep | Their ORM. Modern `Mapped[]` typing style, and the models port to Postgres by changing a URL. |
| **Pydantic** | 2.x | Keep | Already a dependency, and the foundation of the AI operation schema. |
| **uv** | — | Keep | Already the package manager, lockfile committed, used in the Dockerfile. |
| **Docker Compose** | — | Keep | The brief mandates `docker-compose up --build`. |
| **openai** | 1.109.1 | **Finally use it** | Declared in `pyproject.toml` but never imported. The provided key is an OpenAI key. |

### Dropped

| Technology | Reason |
|---|---|
| **Emotion** (`@emotion/react`, `@emotion/styled`) | Used by exactly one component (`LoadingOverlay`) in a Tailwind project. Two styling systems for one spinner. Replaced with Tailwind, dependency and its Babel plugin removed — this also simplifies `vite.config.ts`. |

---

## 2. New backend dependencies (4)

### 2.1 `beautifulsoup4` — HTML parsing

**Why we need it.** The AI engine must read the document, identify claims, and write HTML back.
Claims have no markup — they're detected by text prefixes — so we need a real DOM to walk.

**Why bs4 specifically:** it uses Python's **stdlib `html.parser`**, so there's no C/Rust build step
in the Docker image. The API (`soup.find_all("p")`) is readable by anyone, which matters for the
pair-programming round.

| Alternative | Rejected because |
|---|---|
| **Regex on HTML** | The classic mistake. Cannot reliably handle nested tags, so `<p><strong>1. …` breaks it. Our bold-a-claim feature *creates* exactly that markup. |
| **lxml** | Faster, but needs libxml2 — a system dependency in the Docker image for zero benefit at this scale. |
| **Python `html.parser` raw** | We'd hand-roll tree building. bs4 *is* that, already tested. |
| **Parse on the client with TipTap's JSON** | Moves the engine to JS and forfeits pytest. See §4.2. |

**Verified:** resolves to `beautifulsoup4==4.15.0` on this environment.

### 2.2 `nh3` — HTML sanitising

**Why we need it.** `PUT /versions/{n}` accepts HTML from the browser and stores it. Anything
stored is later rendered. Without sanitising, a crafted save is stored XSS.

**Why nh3:** Rust `ammonia` bindings — fast, maintained, and an **allowlist** design (deny by
default), which is the only safe posture.

**The critical detail:** the allowlist is **derived from what TipTap StarterKit can actually
render**, not from a generic "safe HTML" list. If we strip a tag TipTap supports, saving silently
destroys the user's content — a data-loss bug disguised as security.

Verified from `@tiptap/starter-kit@2.27.1`'s bundled extensions:

```
Nodes  → p · h1–h6 · ul · ol · li · blockquote · pre · code · br · hr
Marks  → strong (bold) · em (italic) · s (strike) · code
Attrs  → start (on ol)
Never  → script · iframe · a · img · table · style · on* handlers
```

A test asserts both seed patents survive sanitising byte-identically, which is how we prove the
allowlist is complete rather than hoping.

| Alternative | Rejected because |
|---|---|
| **bleach** | Deprecated; the maintainers point users to nh3. |
| **Sanitise with bs4** (no new dep) | Hand-rolling a security control. Wrong place to save a dependency. |
| **Don't sanitise** | Stored XSS in an app for law firms. |
| **Sanitise on the client** | Client-side validation isn't a control; the API is reachable directly. |

**Verified:** resolves to `nh3==0.3.6` on Python 3.14; wheels available for the Docker 3.13 target.

### 2.3 `pydantic-settings` — configuration

**Why:** `OPENAI_API_KEY`, `OPENAI_MODEL`, `DATABASE_URL` and size limits need typed, validated,
single-source config. It also gives us a clean way to answer *"is the AI configured?"* — which
drives the no-key degradation path (R4).

| Alternative | Rejected because |
|---|---|
| **`os.getenv` inline** | Untyped, scattered, defaults duplicated, no startup validation. |
| **`python-dotenv` alone** (already a dep) | Loads `.env` but gives no typing or validation. We keep it as pydantic-settings' loader. |

### 2.4 `pytest` (dev) — testing

**Why:** the brief asks for unit tests. pytest is the Python default; fixtures give clean
per-test database isolation, and FastAPI's `TestClient` (via already-installed `httpx`) needs no
extra packages.

| Alternative | Rejected because |
|---|---|
| **`unittest`** | Stdlib, but more boilerplate and worse failure output. No upside. |

**Also adding `ruff`** (dev) — lint + format in one fast tool, replacing black+flake8+isort. Zero
runtime impact.

---

## 3. New frontend dependencies

### 3.1 `zustand` — shared state

**The actual problem it solves.** `ChatPanel` must apply AI output to the TipTap editor and flip
the dirty flag. `Editor` owns that instance. They are **siblings**:

```
App
├── VersionBar   (needs: dirty, version list, save actions)
├── Editor       (owns: the TipTap instance)
└── ChatPanel    (needs: the TipTap instance, setDirty)
```

Without a store, the editor instance and four callbacks drill through `App`, and `App` becomes a
props hub — the exact thing that makes a component hard to modify live in an interview.

**Scoping rule (this is what keeps it from becoming over-engineering):**
> In the store only if **two or more** components need it.

| In the store | Stays local |
|---|---|
| documents, selected doc/version, version list, dirty flag, editor instance, load/save/create | chat messages, attached file, input text (`ChatPanel` only) |

The store is ~40 lines and readable in one screen.

| Alternative | Rejected because |
|---|---|
| **`useState` in `App` + props** | Works, but drills a TipTap instance through the tree. Defensible; zustand is meaningfully cleaner for the sibling-communication case. |
| **React Context** | No new dependency — but re-renders every consumer on any change, and needs a provider + reducer + memoisation to match what zustand does in 40 lines. More code, more concepts. |
| **Redux Toolkit** | Actions, reducers, slices, thunks for ~8 state fields. Textbook over-engineering here. |
| **TanStack Query** | Genuinely good for server cache — but we have 5 endpoints and no polling, refetch or cache-invalidation needs. Concepts we'd never use. |
| **Jotai / Valtio** | Comparable, less common. No reason to prefer them. |

**Verified:** `zustand@5.0.14` available. ~1.2KB gzipped, no peer-dependency constraints.

### 3.2 `vitest` + `@testing-library/react` + `jsdom` (dev)

**Why Vitest:** it reuses `vite.config.ts`, so our TypeScript/JSX/path setup applies with no second
build pipeline. Jest would need its own transform config for the same result.

**Why Testing Library:** tests behaviour through the DOM the user sees, not component internals —
so refactors don't break tests.

**Why jsdom:** already installed transitively and proven to run TipTap headlessly (see §6).

| Alternative | Rejected because |
|---|---|
| **Jest** | Separate config and transform layer duplicating Vite's. |
| **Playwright / E2E** | Real value, but a browser download and CI weight for a challenge. The high-risk logic is on the server, where pytest covers it. Listed in future work. |
| **No frontend tests** | The brief asks for tests, and `.txt` validation plus stale-response handling are real logic. |

**Verified:** `vitest@4.1.10`, `@testing-library/react@16.3.2`, `jsdom@30.0.1` available.

---

## 4. Architectural approaches

Methods and patterns — the decisions that matter more than the libraries.

### 4.1 LLM emits operations; Python applies them

**The approach.** The model never writes HTML. It returns a JSON plan validated against a schema.
Deterministic Python parses the document, applies operations, renumbers, and renders.

```
"Delete claim 3"
   → LLM → { "operations": [ { "kind": "delete_claim", "claim_number": 3 } ] }
   → Python: parse → delete → renumber 1..n → remap cross-refs → render → sanitize
```

**Justification, strongest first:**

1. **Correctness is not negotiable for claim numbering.** Claim numbers are legally meaningful. An
   LLM re-emitting 8 claims will eventually miscount. A `for i, claim in enumerate(claims, 1)` loop
   will not.
2. **The engine tests without an API key.** Every apply rule is a pure function. ~15 of ~20 tests
   need no network — they're fast, deterministic, and run in any reviewer's environment.
3. **Bounded blast radius.** The model cannot alter a claim it wasn't asked to touch, because it
   never emits the document. With full-HTML rewriting, every edit risks every claim.
4. **A truncated response can't destroy work.** `setContent("")` silently empties the editor.

| Alternative | Rejected because |
|---|---|
| **LLM returns the full modified HTML** | Simplest to build, and it's what the brief's wording suggests — but all four points above fail. Non-deterministic numbering, untestable offline, unbounded blast radius, and truncation wipes the document. |
| **LLM returns a diff/patch** | Line anchors are meaningless against TipTap's single-line normalised output (verified in §6). |
| **Client-side TipTap command chains** | Editing logic in JS, forfeiting pytest, in the layer hardest to test. |
| **Fine-tuning / agent loop** | Wildly disproportionate. Explicitly "overly complex". |

**Honest tradeoff:** the operation vocabulary is finite, so some instructions can't be expressed.
Mitigated by designing ops against the **document model** rather than the four examples, plus a
`needs_clarification` status. **An honest "I can't do that" beats a confidently wrong edit** —
especially on a legal document.

### 4.2 OpenAI Structured Outputs

**Why:** the model's JSON is constrained by a schema at generation time, so we get a valid `EditPlan`
or a refusal — never a `JSONDecodeError` at 2am. Pydantic models are both the schema and the runtime
type.

| Alternative | Rejected because |
|---|---|
| **"Return JSON" in the prompt + `json.loads`** | Works ~95% of the time, which is the worst possible reliability. |
| **JSON mode** (`response_format={"type":"json_object"}`) | Guarantees valid JSON, not the right *shape*. Structured Outputs guarantees both. |
| **Function/tool calling** | Effectively the same mechanism with more ceremony for a single call. |
| **Instructor / LangChain** | A framework to wrap one API call. Adds concepts to explain, hides the thing being assessed. |

**Verified:** `client.chat.completions.parse` exists on the **stable** path in `openai==1.109.1`.
The `openai.resources.beta.chat` module **no longer exists** — code written against
`client.beta.chat.completions.parse` (a common older pattern) will raise `AttributeError`.

### 4.3 Two tables; `DocumentVersion` is a mutable draft

Requirement 3 — *"make changes to any of the existing versions and save those changes (without
creating a new version)"* — rules out immutable snapshots. A version is a **named mutable draft**.

| Alternative | Rejected because |
|---|---|
| **One table, `version` column** | Conflates document identity with draft content; no clean way to list versions of a document. |
| **`Document.content` live + snapshot history** | Directly violates requirement 3: saving creates a snapshot. |
| **Event sourcing / append-only log** | Full audit history, at the cost of complexity the brief warns against. Listed in future work. |
| **Server-stored "current version"** | Which version you're *viewing* is client state. Storing it breaks two tabs. |

### 4.4 Both save buttons send the editor buffer

```
Save                 → PUT  /versions/{selected}   overwrite in place    (requirement 3)
Save as new version  → POST /versions              create MAX+1          (requirement 1)
```

**Why this symmetry matters.** The obvious alternative — "new version copies the last *saved*
content" — forces a three-way dialog whenever you have unsaved edits ("save first? discard?"). By
sending the live buffer from both buttons, that entire class of ambiguity disappears. What you see
is what gets saved, to one place or the other.

### 4.5 Claim number as a field, never as text

**In plain English.** Think of a numbered list in Word. You don't type "1.", "2.", "3." yourself —
Word owns the numbers. Delete an item in the middle and everything below renumbers automatically,
because the number was never part of your text.

Our document doesn't work that way: the numbers really are typed into the text. So we fake it.

```
On read   "1. A wireless optogenetic device…"   →   number = 1
                                                    text   = "A wireless optogenetic device…"
On write  number = 1  +  text                   →   "1. A wireless optogenetic device…"
```

While we're editing, a claim's number is **data**, not words. Renumbering is then just counting —
`1, 2, 3…` down the list — instead of finding and rewriting text inside sentences.

**What this prevents.** If numbers stayed as text: "make claim 1 bold" might bold the "1." too and
produce `<strong>1. A wireless…`, which then breaks the parser next time. A find-and-replace of
"1" could mangle claim numbers. Renumbering would mean string surgery on every claim. Keeping the
number as a field makes all of that impossible rather than merely unlikely.

Parse strips `"1. "` into `Claim.number`; render re-injects it. Nothing derives claim identity from
rendered text.

**Why:** renumbering becomes assignment, not string surgery. It also means `replace_text` can never
accidentally corrupt a claim number, and bolding a claim can't swallow its prefix.

**Why not `data-claim-id` attributes?** Verified: StarterKit's `Paragraph` declares no attributes,
so `data-*` is **silently stripped** on the TipTap round-trip. Structure cannot be smuggled through
attributes — this constraint, not preference, forced the design.

### 4.6 Three-region parse: preamble / claims / postamble

**In plain English.** We split the document into three parts and only ever treat the middle one as
claims:

```
┌─────────────────────────────────────┐
│ preamble   — Background, Abstract…  │  ← numbers here are just text
├─────────────────────────────────────┤
│ claims     — the numbered claims    │  ← the only part we renumber
├─────────────────────────────────────┤
│ postamble  — anything after         │  ← numbers here are just text
└─────────────────────────────────────┘
```

**Why it matters.** One of the required features is *"write a background section based on the prior
art file"*. A Background usually starts like this:

> **Background**
> 1. Field of the Invention
> 2. Description of Related Art

Those look exactly like claims — a number, a dot, some text. Without regions, the moment we add a
Background the parser would see "1. Field of the Invention" as **claim 1**, and the next "delete
claim 3" would renumber the Background headings into the patent's claim list. The document would
quietly fall apart.

By deciding *once*, up front, where the claims section begins and ends, everything outside it is
permanently off-limits to claim operations. The bug can't happen.

**Why:** the seed contains *only* a Claims section. "Write a background section" must insert new
content. If the parser treated all numbered paragraphs as claims, a Background containing
"1. Field of the Invention" would become claim 1 and get renumbered into nonsense. Explicit regions
make that structurally impossible.

### 4.7 Bind claim numbers to uids before applying

**In plain English.** Deleting things by position is dangerous, because positions move.

Say a user types **"delete claims 3 and 5"**. Done naively:

```
Start:        1  2  3  4  5  6
Delete #3 →   1  2  4  5  6        (they shift up: old 4 is now #3, old 5 is now #4)
Delete #5 →   1  2  4  5           ✗ we just deleted old claim 6 — the wrong one
```

The user said 3 and 5, meaning the claims **they were looking at**. But after the first delete, "5"
means something different.

**The fix:** before touching anything, give every claim a permanent internal ID — like putting a
barcode sticker on each one. Then translate the user's numbers to stickers *first*, and delete by
sticker:

```
Start:        1(#a) 2(#b) 3(#c) 4(#d) 5(#e) 6(#f)
"3 and 5"  →  sticker #c and sticker #e        ← locked in before any change
Delete     →  1(#a) 2(#b) 4(#d) 6(#f)
Renumber   →  1     2     3     4              ✓ correct
```

Stickers never move, so the answer can't drift. Renumbering happens **once, at the very end**, after
all edits are done — never in between.

This is the same reason you'd use database IDs instead of row positions.

**Why:** `[delete 3, delete 5]` — after deleting 3, the old claim 5 is now claim 4, so a naive
second pass deletes the wrong claim. Resolving all numbers against the **original** parse first
makes multi-operation plans mean what the user saw. Renumbering then happens exactly once, at the
end.

### 4.8 Uncontrolled TipTap + remount by `key`

**In plain English.** Two words to unpack: *uncontrolled* and *remount*.

**Uncontrolled** = we let the editor own the text, instead of React holding a copy and constantly
pushing it back.

The inherited code keeps two copies — React's `content` string and the editor's own content — and
runs a check on every change: *"are these different? then overwrite the editor."* That's like two
people keeping separate copies of the same document and rewriting each other's every few seconds.
It goes wrong because the two copies are never *exactly* equal (the editor tidies up the HTML the
moment it loads it), so the check keeps firing, and **every overwrite jumps your cursor back to the
start while you're typing.**

The fix: stop keeping a second copy. The editor holds the text; we ask for it when we need to save.

**But then how do we load a different version?** That's the **remount**. In React, every component
can have a `key`. Change the key and React doesn't *update* the component — it throws it away and
builds a brand new one:

```tsx
<Editor    key={`${docId}:${version}`} />   // "patent1:v2" → "patent1:v3"
<ChatPanel key={`${docId}:${version}`} />
```

Switching from version 2 to version 3 changes the key, so we get a **fresh editor** with the new
content — no syncing, no overwriting, no cursor jump.

**The nice part:** one line solves three problems at once.

| Problem | Solved by the remount |
|---|---|
| Load the new version's content | New editor starts with it |
| Cursor stuck at a stale position | New editor, new cursor |
| Chat still discussing the old version | New chat panel, empty |

The tradeoff is that the chat history is deliberately thrown away when you switch — which is what
we want, since advice about version 2 is misleading next to version 3.

**Why:** the inherited `useEffect` compares `content` to `editor.getHTML()` and calls `setContent`
when they differ — but the seed string never equals `getHTML()` output, so it fires spuriously and
resets the caret mid-typing. Changing `key={docId}:{versionNumber}` makes React discard and rebuild
the component instead, which loads content, resets the caret, and clears the chat with one
mechanism.

### 4.9 The AI route never writes the database

**Why:** three benefits from one rule. A failed AI call can't corrupt stored work; the user stays in
control of what persists; and the endpoint is a pure function of its inputs, so it's trivial to test.

### 4.10 Fake planner injection instead of mocking

The planner is passed as a function parameter with a default. Tests pass a fake.

**Why not `unittest.mock.patch`:** patching couples tests to import paths and breaks on refactor.
**Why not a Protocol/ABC + DI container:** ceremony for one substitution point.

### 4.11 Monotonic request tokens

Every load carries an incrementing token; responses with a stale token are discarded.

**Why:** switch from Patent 1 to Patent 2 quickly and a slow response for Patent 1 can land *after*
Patent 2 loaded, silently showing the wrong document. The brief promises stress testing across
"common user behaviours" — fast clicking is the most common one there is.

### 4.12 File-backed SQLite via `DATABASE_URL`

**Why:** the scaffold's `:memory:` + `StaticPool` shares one connection across all requests and
loses everything on restart — which makes a *versioning* feature look broken. A file DB fixes both;
`DATABASE_URL` keeps `:memory:` for tests and makes the Postgres migration a config change.

| Alternative | Rejected because |
|---|---|
| **Keep in-memory** | Versions vanish on restart. Undercuts the whole feature. |
| **Postgres in compose** | Matches their production stack, but adds a service, healthchecks and startup ordering to a challenge that ships with SQLite. Called out as the first future-work item instead. |
| **Alembic migrations** | Correct for production; disproportionate for two tables created at startup. Future work. |

### 4.13 Seed stored pre-normalised

The seed is stored exactly as TipTap's `getHTML()` would emit it — single line, no envelope, title
moved to `Document.title`.

**Why:** otherwise the parser faces two different input shapes (pretty-printed on first load,
collapsed after the first save), and the patent title is silently destroyed the first time anyone
presses Save.

---

## 5. Explicitly rejected technologies

Things a reviewer might expect, and why they're absent:

| Not used | Why |
|---|---|
| **LangChain / LlamaIndex** | One prompt, one call, one schema. A framework would hide the assessed logic behind abstractions. |
| **Vector DB / RAG** | The uploaded `.txt` fits in the context window. Retrieval solves a problem we don't have. |
| **Streaming responses** | Nice UX, but it complicates apply-on-completion for a 5–15s call. A thinking indicator is enough. |
| **WebSockets** | That's Option B. We chose Option A. |
| **Redux / MobX** | See §3.1. |
| **Alembic** | See §4.12. |
| **Celery / task queue** | Edits are synchronous and fast enough. |
| **Redis** | Nothing to cache; no shared state between processes. |
| **CI (GitHub Actions)** | Real value in production, none for a zip file. Mentioned in future work. |
| **Auth / SuperTokens** | Out of scope; noted as the integration point with Solve's stack. |

---

## 6. Verification log

Claims in this document were checked by running the real tools, not from memory.

| Claim | Method | Result |
|---|---|---|
| `openai` API surface | Imported `openai==1.109.1` in the project venv | `chat.completions.parse` **exists** (stable); `openai.resources.beta.chat` **does not exist** |
| `setContent` signature | Read `@tiptap/core@2.27.1` type declarations | Positional `(content, emitUpdate?, parseOptions?, options?)`; `errorOnInvalidContent` available |
| TipTap round-trip stability | Ran TipTap + StarterKit over the real seed under jsdom | `parse → getHTML → parse → getHTML` **byte-identical** |
| `getHTML()` output shape | Same run | Single line, 0 newlines, whitespace collapsed, `<h1>` kept, `<!DOCTYPE>`/`<head>`/`<title>` **stripped** |
| Seed structure | Same run | Patent 1 → 19 `<p>`, 8 claims, claim 1 spans 5 paragraphs |
| StarterKit schema | Read bundled extension list | Confirmed node/mark set in §2.2; **`history` is included**, so native `Cmd+Z` works |
| Backend deps resolve | `uv pip install --dry-run` | `beautifulsoup4==4.15.0`, `nh3==0.3.6`, `pydantic-settings==2.15.0`, `pytest==9.1.1` — clean |
| Frontend deps available | `npm view` | `zustand@5.0.14`, `vitest@4.1.10`, `@testing-library/react@16.3.2`, `jsdom@30.0.1` |
| Client install state | Inspected `client/node_modules` | Complete — 229 packages, `.bin` present |

### Environment note

The local venv runs **Python 3.14.7**; the Dockerfile targets **`python:3.13-slim`**, and
`pyproject.toml` requires `>=3.13`. Both work, but "works locally, differs in Docker" is exactly how
submissions break on a reviewer's machine. **Phase 0 pins local development to 3.13** to match the
image that reviewers actually run.

---

## 7. Open items requiring a live API call

Everything else is settled. These need the provided key and are resolved early in phase 4, before
any code depends on them:

1. **Does `gpt-5.2-2025-12-11` accept our exact `parse` call?** Reasoning models reject
   `temperature`; we send none. Confirm the model id is valid on the provided key.
2. **Is `reasoning_effort` supported, and does `"low"` help latency?** A nice-to-have. If
   unsupported, omit it — no design depends on it.
3. **Real end-to-end latency**, which sets the client timeout (planned ~60s).

All three are isolated inside `ai/planner.py`. If any surprises us, one file changes.

---

## 8. Summary

| Layer | Technology |
|---|---|
| Editor | TipTap 2.27 + StarterKit *(inherited)* |
| UI | React 18 + TypeScript 5.6 + Tailwind 4 + Vite 5 *(inherited)* |
| Client state | **zustand** — shared state only |
| HTTP | axios *(inherited)* |
| API | FastAPI 0.115 + Pydantic 2 *(inherited)* |
| ORM / DB | SQLAlchemy 2.0 + **file-backed SQLite** via `DATABASE_URL` |
| HTML | **beautifulsoup4** (parse) + **nh3** (sanitise) |
| Config | **pydantic-settings** |
| LLM | openai 1.109 + **Structured Outputs** → validated operation plans |
| Tests | **pytest** (backend) + **vitest** & Testing Library (frontend) |
| Lint | **ruff** (Python) + ESLint 9 flat config (TS) |
| Ops | Docker Compose *(inherited)* |

**Five new runtime dependencies** (`beautifulsoup4`, `nh3`, `pydantic-settings`, `zustand`, and
finally using `openai`), plus test and lint tooling. Everything else was already here.

The through-line: **the LLM is used for language, and only language.** Structure, numbering,
ordering and persistence are deterministic Python — which is what makes this testable, explainable,
and safe to run on a legal document.
