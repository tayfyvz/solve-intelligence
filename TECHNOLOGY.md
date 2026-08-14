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

A recurring theme: **we add almost nothing.** Five backend libraries, one frontend library, plus
test tooling. Everything else was already in the scaffold.

**One of those five is not small, and this document says so plainly.** `langgraph` brings 21
transitive packages with it (§2.5). It is the only dependency here that costs more than a rounding
error, and it is the only one whose section leads with the measured bill rather than the benefit.
It passes criterion 2 — the graph is seven named nodes and three conditional edges, and drawing
it on a whiteboard takes under a minute — but it would not have passed criterion 3 if the pipeline
were a straight line. It isn't: it has a cycle.

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

## 2. New backend dependencies (5)

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
| **Parse on the client with TipTap's JSON** | Moves the engine to JS and forfeits pytest. See §4.1. |

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
Attrs  → start AND type (both on ol)
Never  → script · iframe · a · img · table · style · on* handlers
```

**`ol` takes `type` as well as `start`** — verified in `ordered-list.ts:77-90`, which declares both.
An earlier draft of this list omitted `type`, and omitting an attribute StarterKit emits means a
save silently rewrites the user's list numbering. **`h4`–`h6`, `blockquote`, `pre`, `code` and `hr`
are on the list for the same reason** (`heading.ts:52` declares levels 1–6): every one of them is
reachable *by accident* — typing `#### `, `> `, ```` ``` ```` or `---` at the start of a line — so a
generic "safe HTML" list would have deleted a user's content the first time they used a markdown
shortcut. This is C2 in PLAN §2.1, and **this block is the source of truth**; `DESIGN.md §7` defers
to it.

**Two honest gaps, stated rather than papered over.** `code[class="language-*"]` is stripped, so a
code block loses its language hint — cosmetic, and recorded in PLAN §2.4. And nh3's `attributes=`
argument does **not** fully replace its attribute handling: `title` and `lang` are permitted on
every allowed tag regardless of what we pass. Both are inert, so this is documented and tested
rather than fought.

A test asserts both seed patents survive sanitising byte-identically, and a second asserts each
accidentally-reachable element survives a round-trip — which is how we show the allowlist is
complete rather than hoping. It was also confirmed by hand in Step 6: a blockquote applied in the
editor survived save **and a full page reload**.

| Alternative | Rejected because |
|---|---|
| **bleach** | Deprecated; the maintainers point users to nh3. |
| **Sanitise with bs4** (no new dep) | Hand-rolling a security control. Wrong place to save a dependency. |
| **Don't sanitise** | Stored XSS in an app for law firms. |
| **Sanitise on the client** | Client-side validation isn't a control; the API is reachable directly. |

**Verified:** locked at `nh3==0.3.6` on Python 3.13, and imported inside the `python:3.13-slim`
image — the `cp38-abi3` manylinux wheel installs with no Rust toolchain.

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

### 2.5 `langgraph` — the AI pipeline

This is the one expensive dependency in the project, so the cost comes first.

**Measured cost.** `uv add langgraph` in an isolated copy of `server/`:

| Metric | Before | After |
|---|---|---|
| Packages in `uv.lock` | 35 | 56 (**+21**) |
| Distributions in the `--no-dev` venv | 33 | 54 (**+21**) |
| `--no-dev` venv size | 61.5 MiB | 78.5 MiB (**+17 MiB**) |
| Version conflicts | — | **zero** |

> The size row measures the **`--no-dev`** venv — the one the image actually ships. An earlier
> revision reported "77 MB → 89 MB (+12 MB)" by comparing a local **dev** venv baseline (which
> carries the PEP 735 `dev` group) against a `--no-dev` delta. Two different environments, so the
> subtraction was meaningless. Measure both ends the same way.

The 21 are `langgraph==1.2.11`, `langchain-core==1.5.4`, `langgraph-checkpoint==4.2.0`,
`langgraph-prebuilt==1.1.0`, `langgraph-sdk==0.4.2`, `langchain-protocol==0.0.18`,
`langsmith==0.10.18`, `orjson==3.11.9`, `ormsgpack==1.12.2`, `tenacity==9.1.4`, `xxhash==4.0.0`,
`zstandard==0.25.0`, `websockets==15.0.1`, `uuid-utils==0.17.0`, `jsonpatch==1.33`,
`jsonpointer==3.1.1`, `pyyaml==6.0.3`, `requests==2.34.2`, `requests-toolbelt==1.0.0`,
`urllib3==2.7.0`, `charset-normalizer==3.5.0`. **`openai`, `pydantic`, `fastapi`, `sqlalchemy` and
`httpx` all resolve at their existing versions** — nothing in the scaffold moved, which was the
condition for adopting it at all.

**Why we need it — there is a genuine cycle.** The pipeline is not a chain:

```
                         ┌───────── retry (bounded, the judge's complaint fed into the prompt)
                         ▼                                                   │
  understand ──┬──▶ retrieve ──▶ draft ──▶ judge ──(fail, attempts left)─────┘
               │        │                    │
               │        │                 (pass)
               │        └──▶ answer ─────────┼──────────────▶ verify ──▶ end
               ├──▶ plan_ops ────────────────┤
               └──(unresolved)───────────────┘
```

Two structural facts drive the choice:

1. **An LLM-as-judge node scores generated claim text and routes *back* to `draft` on failure.**
   Bounded at two attempts by default, with the judge's complaint appended to the retry prompt.
   That is a cycle with state that accumulates across iterations.
2. **The `understand` node fans out to three mutually exclusive branches** — generative drafting,
   mechanical operations, or a question — plus a fourth exit straight to `verify` when it could not
   resolve the request at all. *(This section said "the router node". There is no router: it was
   widened into `understand`, for the reason in §4.18.)*

A cycle with a bounded retry budget, per-node responsibility separation, and a shared typed state
object is exactly the shape a graph library exists for. Written as `if/elif` plus a `while
attempts < 2` loop, the same logic becomes one long function where the retry budget, the state
mutation and the branch selection are interleaved — an honest reading of that code is harder than
an honest reading of six named node functions plus a routing table. It would *fake* a graph badly
rather than avoid being one.

**In plain English.** LangGraph is a way of writing "first do A, then B, and if B is unhappy go
back and do A again — but no more than twice." Each step is an ordinary Python function that takes
a state dict and returns the fields it changed. The library supplies the wiring and the loop
budget; it does not supply prompts, chains, agents, retrievers or memory, and we use none of those.

**What we deliberately do not use from it.** No checkpointer, no `interrupt()`, no prebuilt agent,
no LangSmith tracing (it is a transitive package, not an enabled feature), no `langgraph-sdk`
server. Human-in-the-loop happens **between two graph runs**, not inside one — see §4.14, which is
where the verified reasons live.

**Cost we accept, stated plainly — now measured, not estimated.** A generative request makes **3–5
LLM calls** (understand → draft → judge, plus up to two more draft/judge pairs). At the measured
per-call latency of **1.5 s median / 6.7 s max** (§7, PLAN §20.7) that is **~7.5 s typically and
~33 s at the observed worst case**, against **1–2 s for a single mechanical edit** — not the 5–15 s
per call this section originally assumed. The keyword fast-path (§4.17) removes the understanding
call from the common cases. We chose a slower, checked generation over a fast, unchecked one — on
claim text, that is the right trade, and the UI shows which stage is running so a multi-second wait
does not look like a hang.

| Alternative | Rejected because |
|---|---|
| **Plain `if/elif` dispatch** (zero deps) | The honest default, and it would have won if the pipeline were a straight line. It isn't. The judge→draft cycle means hand-managing a retry counter, the accumulated failure feedback, and the branch selection in one function — the "obvious solution" stops being the obvious one at exactly this point. |
| **LangChain LCEL** (pipe-composed chains) | Composes chains, but LCEL is fundamentally a **DAG** — expressing a conditional cycle back to an earlier step is where it stops being ergonomic. It also drags in the retriever/memory/agent surface area we don't want. LangGraph is the part of that ecosystem that matches our shape, and we take only that part. |
| **Hand-rolled node runner** (~80 lines, zero deps) | Genuinely tempting: a dict of node functions, a router table, a step cap. But we'd be writing state merging, cycle-limit enforcement and error propagation ourselves, then testing our runner instead of our patent logic. Reviewers also read a known library faster than my bespoke one. |
| **Temporal / Prefect** | Durable execution, retries and observability for real — and completely disproportionate. Both add a service to `docker-compose`. Our "workflow" is a single request lasting under a minute with no durability requirement (§4.14). |
| **OpenAI Assistants / agent loop** | Hands control of the loop to the provider, hides the assessed logic, and is explicitly the "overly complex" direction the brief warns against. |

**Verified:** `uv add langgraph` → `langgraph==1.2.11`, +21 packages, lock 35 → 56,
`--no-dev` venv 61.5 → 78.5 MiB, no version conflicts (§6).

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
| documents, selected doc/version, version list, dirty flag, editor instance, load/save/create | chat messages, attached file, input text, the pending AI proposal (`ChatPanel` only) |

**This section used to end "the store is ~40 lines and readable in one screen". It is 617,** and
that is worth correcting rather than quietly deleting, because the number was the argument. The
scoping rule held — every field above is read by two or more components — but the *actions* grew:
pagination, create, rename, two saves, and a request-ordering guard on each. The one-screen claim is
gone; the shape it was defending is not, and it is what §4.11 and the "shared state only" invariant
in `CLAUDE.md` still enforce. Recorded as C25 in PLAN §2.3.

| Alternative | Rejected because |
|---|---|
| **`useState` in `App` + props** | Works, but drills a TipTap instance through the tree. Defensible; zustand is meaningfully cleaner for the sibling-communication case. |
| **React Context** | No new dependency — but re-renders every consumer on any change, and needs a provider + reducer + memoisation to match what zustand does with a plain hook. More code, more concepts. |
| **Redux Toolkit** | Actions, reducers, slices, thunks for ~8 state fields. Textbook over-engineering here. |
| **TanStack Query** | Genuinely good for server cache — but we have a handful of endpoints and no polling, refetch or cache-invalidation needs. Concepts we'd never use. |
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
| **No frontend tests** | The brief asks for tests, and `.txt` validation, the consent gate and stale-response handling are real logic. |

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
   → Python: parse → delete → renumber 1..n → remap cross-refs → render → sanitize → verify
```

**Justification, strongest first:**

1. **Correctness is not negotiable for claim numbering.** Claim numbers are legally meaningful. An
   LLM re-emitting 8 claims will eventually miscount. A `for i, claim in enumerate(claims, 1)` loop
   will not.
2. **The engine tests without an API key.** Every apply rule is a pure function. The majority of
   the suite needs no network — fast, deterministic, and runnable in any reviewer's environment.
   This is why `ai/document.py` may never import `openai`, and why the graph's node functions take
   the planner as a parameter (§4.10).
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
type. This holds for every LLM node in the graph: the router returns a typed branch decision, the
drafter returns typed claim text, the judge returns a typed score plus reason.

| Alternative | Rejected because |
|---|---|
| **"Return JSON" in the prompt + `json.loads`** | Works ~95% of the time, which is the worst possible reliability. |
| **JSON mode** (`response_format={"type":"json_object"}`) | Guarantees valid JSON, not the right *shape*. Structured Outputs guarantees both. |
| **Function/tool calling** | Effectively the same mechanism with more ceremony. We want a schema-constrained *return value*, not a model-driven loop over callable tools — see §5 for why the tool-calling framing is actively wrong here. |
| **Instructor** | A wrapper around the feature `openai` already exposes natively on the stable path. Nothing to gain. |
| **LangChain's model wrappers** (`ChatOpenAI`, `with_structured_output`) | We **do** use LangGraph (§2.5), but only its graph runtime. Every LLM call still goes through the raw `openai` client and `client.chat.completions.parse` — one provider, one API, one place to look when a call misbehaves. `langchain-core` arrives as a transitive dependency; we do not build on it. |

**Verified:** `client.chat.completions.parse` exists on the **stable** path in `openai==1.109.1`,
and that is what `llm.py` calls.

*Correction (C21).* This paragraph used to add that the `openai.resources.beta.chat` **module** is
gone and therefore `client.beta.chat.completions.parse` "will raise `AttributeError`". The first
half is true; **the second does not follow and is false** — `client.beta.chat` is a live alias and
`.parse` is a working bound method. The design is unaffected either way, because we use the stable
path for its own sake. It is corrected here because a justification that is wrong is worse than no
justification when the document is defended out loud.

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

**What this prevents.** A find-and-replace of "1" could mangle claim numbers. Renumbering would
mean string surgery inside every claim's sentence. Keeping the number as a field makes both
impossible rather than merely unlikely, and it is what lets `delete_claim` be a list operation.

Parse strips `"1. "` into `Claim.number`; render re-injects it. Nothing derives claim identity from
rendered text.

**One thing an earlier version of this section claimed, which is false and worth correcting
precisely.** It said that bolding a claim would produce `<strong>1. A wireless…` and *"then break
the parser next time"*. **The first half happens on every bolded claim; the second half does not.**
`format_claim` marks the whole block, so render legitimately emits
`<p><strong>1. A wireless…</strong></p>` — the leading text node is **inside** the `<strong>`, and
that is the common case, not an edge case.

The parser handles it by design. It reads block text through BeautifulSoup's `get_text()`, so it
sees through marks entirely; and `_strip_prefix` **descends through leading inline elements**,
consuming the prefix's characters in document order and dropping the wrapper if it empties. The
guarantee is not "`<p><strong>1. …` never occurs" — it occurs constantly. The guarantee is the pair
of round-trip invariants in PLAN §2.2 (C20): **identity on canonical input** (T1/T2) and
**idempotence in general** (T3), with `VF-E5` enforcing the second at runtime. Test **T12** is the
bolded-prefix case specifically.

Claiming the stronger property would have been easy to write and impossible to defend, since a
reviewer can produce a counter-example in one click.

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
all edits are done — never in between. Then cross-references are remapped through the old→new map.

This is the same reason you'd use database IDs instead of row positions.

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

The chat history — **and any un-applied AI proposal** — is deliberately thrown away when you
switch, which is what we want: a proposal computed against version 2 must never be applied to
version 3.

### 4.9 The AI route never writes the database

**Why:** three benefits from one rule. A failed AI call can't corrupt stored work; the user stays in
control of what persists; and the endpoint is a pure function of its inputs, so it's trivial to test.

**This survives the auto-versioning decision in §4.16.** The proposal route stays read-only: it
returns HTML and writes nothing. The version row for a generative edit is created by the
*confirming* request, after the apply succeeds — a different endpoint, an explicit user action, and
still never inside the graph.

### 4.10 Fake planner injection instead of mocking

The planner is passed as a function parameter with a default. Tests pass a fake. With the graph in
place this matters more, not less: each LLM-backed node (`route`, `draft`, `judge`) receives its
model callable the same way, so the **entire graph — cycle, retry budget, routing and all — runs
in tests with no network.** A fake judge that fails once and passes on retry is how we test the
cycle deterministically.

**Why not `unittest.mock.patch`:** patching couples tests to import paths and breaks on refactor.
**Why not a Protocol/ABC + DI container:** ceremony for a handful of substitution points.

### 4.11 Monotonic request tokens

Every load carries an incrementing token; responses with a stale token are discarded.

**Why:** switch from Patent 1 to Patent 2 quickly and a slow response for Patent 1 can land *after*
Patent 2 loaded, silently showing the wrong document. The brief promises stress testing across
"common user behaviours" — fast clicking is the most common one there is. With generative requests
running up to ~30 s (§2.5), the window for a stale landing is wider than before, so this is load-
bearing rather than defensive.

### 4.12 File-backed SQLite via `DATABASE_URL`

**Why:** the scaffold's `:memory:` + `StaticPool` shares one connection across all requests and
loses everything on restart — which makes a *versioning* feature look broken. A file DB fixes both;
`DATABASE_URL` keeps `:memory:` for tests and makes the Postgres migration a config change.

| Alternative | Rejected because |
|---|---|
| **Keep in-memory** | Versions vanish on restart. Undercuts the whole feature. |
| **Postgres in compose** | Matches their production stack, but adds a service, healthchecks and startup ordering to a challenge that ships with SQLite. Called out as the first future-work item instead. |
| **Alembic migrations** | Correct for production; disproportionate for two tables created at startup. Future work. |

**One database, one lifecycle.** This is also why the graph has no checkpointer (§4.14): a
`SqliteSaver` would introduce a *second* SQLite file with its own schema, migration story and
garbage-collection needs, for state that lives under a minute.

### 4.13 Seed stored pre-normalised

The seed is stored exactly as TipTap's `getHTML()` would emit it — single line, no envelope, title
moved to `Document.title`.

**Why:** otherwise the parser faces two different input shapes (pretty-printed on first load,
collapsed after the first save), and the patent title is silently destroyed the first time anyone
presses Save.

### 4.14 Human-in-the-loop lives *between* two graph runs — no checkpointer

**The decision.** Generative edits need the user to see a proposal before it lands. LangGraph
offers a built-in way to do that — `interrupt()` plus a checkpointer that persists the paused graph
so it can resume later. **We use neither.** Instead:

```
Run 1  POST /api/ai/chat      instruction → understand → retrieve → draft ⇄ judge → verify
                              → proposal (operations + summary — NO HTML)
                              ↓ nothing persisted; the graph run ends
       client holds the proposal and renders it as a plain-language summary
Run 2  POST /api/ai/apply     user clicks Apply → re-validate → apply → verify → new version
```

Two details this diagram used to get wrong. The route is **`/api/ai/chat`**, not `/api/ai/edit` —
run 1 also answers questions and asks clarifying ones, so naming it "edit" undersold it. And the
proposal carries **no HTML at all**: only operations and a summary. The only HTML-shaped field
anywhere in the AI surface is the top-level `html`, and only an *applied* outcome has one. That is
what makes "the AI cannot change your document until you click Apply" a structural fact rather than
a promise about client code.

**In plain English.** Rather than pausing a running program and hoping we can wake it up later, we
finish, hand the answer back, and start a fresh program when the user decides. The server keeps no
memory of the pause; the browser holds the proposal, because the browser is where the human is.

**Why — four verified reasons, not preferences.**

1. **`interrupt()` re-executes its whole node on resume.** A node that plans *and* interrupts calls
   the LLM twice, and — because the second call is a fresh generation — the user can approve
   **plan A and receive plan B**. On a legal document, silently applying something other than what
   was consented to is the worst failure mode in this project. Avoiding it would mean splitting
   every generative node in two purely to satisfy the framework.
2. **`InMemorySaver` resumed from a fresh process fails *inside* the graph.** Verified: it raises
   `KeyError: 'instruction'` from within node execution, not a clean "thread not found" we could
   turn into a readable 409. And `server/Dockerfile` runs `uvicorn --reload`, so **every source
   edit during development wipes every pending confirmation.** A HITL mechanism that breaks when
   you save a file is not a mechanism.
3. **`SqliteSaver` is not bundled.** It needs `langgraph-checkpoint-sqlite`, `aiosqlite` and
   `sqlite-vec` — three more packages on top of the 21 — plus a second database lifecycle (§4.12)
   and a garbage-collection story for threads the user abandoned by closing the tab. All to
   remember something for thirty seconds.
4. **Statelessness is a feature here.** With no server-side pending state, restarts are harmless,
   two tabs can't collide over a thread id, and the endpoint stays a pure function of its inputs —
   the same property §4.9 buys us.

**The cost, honestly.** The proposal HTML travels to the client and back, so the apply request is
larger, and a proposal is lost if the tab closes (acceptable — it was never saved work). We also
re-plan on the apply run rather than reusing a paused graph's state. Both are cheap next to the
consent-mismatch risk in point 1.

| Alternative | Rejected because |
|---|---|
| **`interrupt()` + `InMemorySaver`** | Points 1 and 2 above. Fails on `--reload`, and can approve-A-apply-B. |
| **`interrupt()` + `SqliteSaver`** | Point 3: three more packages, a second DB, and thread GC for sub-minute state. |
| **Server-side dict of pending proposals** | A hand-rolled checkpointer with the same restart and GC problems, minus the library's testing. |
| **No confirmation at all** | Generative text rewriting claim language without consent. Non-starter (§4.16). |

### 4.15 A deterministic `verify` step after every apply

**The decision.** After the apply pipeline renders, a pure Python `verify()` runs over the *result*
and checks mechanically:

- claim numbers are exactly `1..n`, sequential, no gaps, no duplicates;
- every `claim N` cross-reference points at a claim that exists, and no claim references itself or
  a later claim;
- no claim is empty or whitespace-only;
- the claims region still parses back to the same claim count the apply expected.

Failures are returned as `warnings` alongside the result, or — for numbering corruption, which
should be impossible — as an `error` that suppresses `html` entirely, so the document is left
byte-identical.

**In plain English: two different questions need two different checkers.**

| Question | Who answers it | Why |
|---|---|---|
| *"Is this claim text any good?"* | The LLM judge, inside the cycle | Quality is a language judgement. Python can't score whether an amended claim reads like patent prose. |
| *"Is this document structurally valid?"* | `verify()`, deterministic Python | Correctness is arithmetic. An LLM asked "are the numbers sequential?" will sometimes say yes when they aren't. |

Layering them is the whole point. The judge gates *generation*; verify gates *the artefact*. Neither
can cover for the other, and verify is the one that runs on **every** request, including purely
mechanical ones that never touch the drafter.

**Why it's cheap to justify:** it's a pure function over a parsed document, so it lives in
`ai/document.py`, needs no API key, and is trivially unit-tested — including against the known
cross-reference error in the seed (patent 1's claim 7 says "claim 5" where it means claim 6). That
bug is *expected output* for verify: it proves the checker sees real problems in real documents
rather than only synthetic ones.

| Alternative | Rejected because |
|---|---|
| **Trust the apply pipeline** | It's deterministic, but "deterministic" and "correct" aren't synonyms — verify is what catches an operation-ordering bug in *my* code, which is the failure the tests can't anticipate. |
| **Ask the LLM to check its own work** | Another call, non-deterministic, and it can be wrong in the reassuring direction. |
| **Hard-fail on any warning** | A cross-reference to a deleted claim is a real editorial decision, not a crash. Surfacing it and letting the user judge is correct (`DESIGN.md` §5.4 rule 4). |

### 4.16 Sticky per-version consent — decided in Python, from one boolean

**This section previously described a different design** — a gate keyed on `GENERATIVE_KINDS`, where
an operation that wrote new prose forced a proposal and a new version, and a mechanical one did not.
That gate is **retired**. It was wrong in *both* directions, which is why it is worth recording
rather than quietly replacing:

- **Too loose.** "Delete claim 3" is mechanical by kind, so it applied straight into the buffer with
  no confirmation and no restore point. Deleting a claim is one of the most destructive things this
  tool can do, and it was the case the gate waved through.
- **Too tight.** Bolding four claims one at a time on an already-confirmed version produced four
  proposals and four versions — the version-sprawl the feature exists to avoid.

**The shipped rule: consent is sticky per version.** The **first** AI change on a version is
confirmed by the user and creates a restore point; **every change after that on the same version is
ordinary editing** — applied straight into the buffer, no prompt, no further version. Any navigation
(different patent, different version) clears it, and the next change is confirmed again.

```
v2  "Make claim 1 bold"   → proposal → Proceed → applies, creates v3, consent moves to v3
v3  "Delete claim 3"      → applies immediately. Still v3. "Not saved yet."
v3  → switch to v1 → "Make claim 1 bold" → proposal again → Proceed → v4
```

That is `CT1`–`CT14` and it is walked by hand in `PHASE6-MANUAL.md`. **The arithmetic that justifies
it:** a seven-instruction editing session costs **seven** versions under "version every AI edit",
**one** under "never version", and **two** under this rule — each of the two created by a human
clicking Proceed.

**Why the decision is still made in Python, and why it is now *one boolean*.** The client derives a
`ConsentKey {documentId, versionNumber}` and compares it against the live store, sending a single
`consented` flag. The server reads that flag and nothing else — **not the operation kinds, not the
understanding's intent, and not anything the model said about itself.** The gate is therefore still
outside the thing being gated, which is the property that matters when an uploaded `.txt` is
attacker-controlled text: **nothing written in an uploaded file can talk its way past it**, because
the model has no field with which to express an opinion about consent. A prompt injection can at
worst cause a *wrong* operation to be proposed, which the user sees and rejects.

**Why a derived key rather than a stored flag.** A bare `aiConsentedVersion: number` is ambiguous
across patents — consent granted on patent 1 would carry to patent 2 — and it is a flag someone must
remember to clear, so a future `selectVersionByName()` that nobody wires up fails **open**. A key
derived from live store state has no reset call site at all and fails **closed**. Nothing about
consent is stored anywhere.

**`GENERATIVE_KINDS` survives, demoted.** It no longer decides anything. Its one remaining job is
`AiProposal.authors_new_text`, which tells the confirmation card whether the plan **writes new
prose** or merely rearranges text the user already approved — the single most useful thing to tell a
patent attorney before they click Proceed. It was renamed from `needs_confirmation()` to
`authors_new_text()` so the name says what it computes.

**Version created after apply, never before.** Apply → verify → then the version row. If the apply
raises or verify hard-fails, no row exists. **There are no orphan versions.**

| Alternative | Rejected because |
|---|---|
| **Gate on `GENERATIVE_KINDS`** | The shipped-then-retired design above: waved through `delete_claim`, and charged a version per bolded claim. |
| **Model returns `requires_confirmation`** | Puts the safety gate inside the model's output, which is exactly what untrusted uploaded text can influence. |
| **Confirm every edit** | Four clicks to bold four claims. Consent fatigue makes the dialog meaningless for the one case that matters. |
| **Confirm nothing; rely on undo** | Undo is a per-tab, in-memory stack. A version is durable, and the generative case is where the old wording matters most. |
| **Version *every* AI edit** | Version list fills with "made claim 2 bold". Devalues the feature Task 1 exists to provide. |
| **Store `aiConsentedVersion` in the store** | Ambiguous across patents, and a flag that must be cleared fails open when someone forgets. |
| **Create the version first, then fill it** | Orphan versions on any failure. Strictly worse for a single saved round-trip. |

### 4.17 Selection is read-only context — never positions on the wire

**The decision.** When the user has text selected in the editor, we send the selected **plain
text** (capped, as context) and nothing else. We never send ProseMirror positions (`from`/`to`), and
the server never returns positions. The server's answer is always the same shape it is without a
selection: operations over the document model.

**Why not positions.** A ProseMirror position is an index into a *specific* document state. Between
the user selecting text and the response arriving — measured at 1.5 s to ~33 s depending on the
branch (§2.5, §7) — they can type, and
every position after the caret shifts. Applying a stale `from`/`to` doesn't fail loudly; it
**silently formats or replaces the wrong span.** The document model has stable identities (claim
uids, §4.7); character offsets do not. Sending offsets across an asynchronous boundary is the same
class of mistake as deleting claims by position.

**"Make the selected text italic" is handled on the client.** It never reaches the AI at all —
it's `editor.chain().focus().toggleItalic().run()`, one line, instant, and correct by construction
because the selection is applied in the same tick it was read. Round-tripping a formatting command
through an LLM to get back an operation that formats a span we'd then have to re-locate is slower
and less reliable than the editor doing what editors do.

**What selection *is* good for:** disambiguation. "Shorten this" with claim 4's text selected lets
the router and drafter see which claim is meant, and Python resolves that text back to a claim uid
before any operation is bound. The selection informs *which claim*; it never determines *which
characters*.

| Alternative | Rejected because |
|---|---|
| **Send `from`/`to` and apply server-side** | Stale offsets silently corrupt the wrong region. The failure is invisible. |
| **Send the ProseMirror JSON of the selection** | Richer, same staleness problem, plus a second document representation to maintain. |
| **Ignore selection entirely** | Loses genuinely useful disambiguation for free. |
| **Route "make selected text italic" through the AI** | An LLM call and up to 30 s for something the editor does in a millisecond. |

### 4.18 Deterministic fast-path in front of `understand` — 3 patterns, not 6

**This section previously described "a keyword fast-path in front of the *router*", with six
patterns.** Two things changed and both are worth stating: there is no router node any more, and two
of the six patterns were demonstrably wrong.

**There is no router.** A separate `route` node was widened into **`understand`** (PLAN §1.5 row 25).
Splitting *"what does this person want?"* into "classify it" and "resolve it" costs a second LLM call
— ~3 s of pure overhead at the measured median — to answer two halves that cannot be answered
independently: you cannot classify `edit_ops` vs `generate` for *"tighten the last claim"* without
first working out **which** claim. Routing is a projection of understanding: `intent` is one field of
`Understanding`. `RouteChoice` was deleted; the node count stayed at seven.

**The fast-path is therefore an *understander*, not a router.** It returns a fully resolved,
parse-validated `Understanding` — or `None`. Three anchored patterns, covering the mechanical
instructions users repeat most: bold/italic/strike a numbered claim, delete a numbered claim, and
the un-format of the first.

**The two deleted patterns, and why.** Patterns 5 and 6 were question and summarise heuristics, and
they **misrouted compound requests**:

- *"what is claim 3 about, and make it bold?"* matched the question pattern → `answer` → the edit
  was silently dropped.
- *"summarise claim 4 then shorten it"* matched the summarise pattern, because `shorten` was not in
  its negative lookahead.

A pattern that has to enumerate every verb it must *not* fire on is a classifier, and a bad one.
Both were deleted rather than patched.

**The three survivors are constrained so they cannot repeat that failure.** Each can only return a
**fully resolved** understanding or `None`; each **refuses to fire while a clarifying question is
pending** (so an answer to "which claim?" always reaches the model as an answer, with the question
quoted); and each **refuses to fire on a claim number the parse does not contain**.

**Why it's safe — and this is the only reason it's allowed.** *The fast-path is not the safety gate.*
`gate_understanding` runs on **every** path including this one, and only ever moves towards
`resolved=False`. Downstream is unchanged: schema validation, uid binding against the original parse
(§4.7), one deterministic renumber, `verify()` (§4.15), and the consent boolean (§4.16) — which the
fast-path does not and cannot influence, because consent is decided by the client's `consented` flag
and not by how the instruction was understood. The worst case for a false positive is a *wrong
mechanical edit*: visible, one `Cmd+Z` away, and never saved without an explicit Save.

**What it actually buys, measured.** ~1.5 s on **exactly two of the four** README acceptance
instructions, and nothing else (§7). That is a real but modest saving, which is the honest framing —
and it is why PLAN §1.5 row 26 records that deleting `fast_understanding` and its call site is a
**two-line change** if it ever becomes a source of doubt. Live evidence that it is working: Step 6's
walk shows *"Make claim 1 bold"* resolving with `routed_by=keyword` and **zero** LLM calls in
`understand`.

**Kept deliberately dumb.** Narrow anchored patterns, three of them, no fuzzy matching, no synonym
lists. If the set grows to where it cannot be recited, it has become a second classifier competing
with the model — at which point it should be deleted, not extended. It has already shrunk once.

| Alternative | Rejected because |
|---|---|
| **Always call `understand`** | Correct and simple, and the fallback if the fast-path is ever doubted. Costs ~1.5 s on the two most-demoed instructions. |
| **Six patterns (the previous design)** | Two of them misrouted compound requests, dropping an edit the user asked for. Measured, not theorised. |
| **Keyword-only understanding (no LLM)** | Cannot handle "shorten claim 4 a bit" or anything phrased off-script. The model is what makes this a chat assistant rather than a command line. |
| **A separate `route` node before `understand`** | Two LLM calls to answer one question, and the halves are not independent. |
| **Cache decisions by instruction hash** | Another store and a stale-cache bug class, for a saving three regexes already get. |

---

## 5. Explicitly rejected technologies

Things a reviewer might expect, and why they're absent:

| Not used | Why |
|---|---|
| **LangChain / LlamaIndex as an application framework** | We adopt **LangGraph** for the graph runtime (§2.5) and nothing else from that ecosystem. LangChain's chains, agents, retrievers, memory and model wrappers are all declined: every LLM call goes through the raw `openai` client with Structured Outputs (§4.2). The distinction is deliberate — we took the piece that models a *cycle*, and left the piece that hides the API. LlamaIndex is retrieval-first, and we have nothing to retrieve. |
| **LangGraph checkpointers** (`InMemorySaver`, `SqliteSaver`) and `interrupt()` | Verified failure modes: `interrupt()` re-executes its node on resume (approve plan A, receive plan B); `InMemorySaver` in a fresh process raises `KeyError: 'instruction'` inside the graph, and `--reload` wipes pending state on every file save; `SqliteSaver` needs 3 more packages and a second DB lifecycle. HITL lives between two graph runs instead — §4.14. |
| **Tool-calling / function-calling agent loop** | The strongest-looking alternative, and still wrong here. Our six operations **already are** the vocabulary — exposing them as callable tools adds a model-driven loop over a fixed schema we can validate in one shot. And a retrieval tool is strictly worse than context: the whole document is ~2.7 KB and fits comfortably in the prompt, so "let the model fetch the part it needs" replaces reading with guessing. Structured Outputs gives us the schema guarantee without handing the model the control flow. |
| **Vector DB / RAG** | The uploaded `.txt` and the patent both fit in the context window with room to spare. Retrieval solves a problem we don't have, and chunking a legal document is a way to lose the sentence that mattered. |
| **Streaming responses** | Nice UX, but it complicates apply-on-completion: we apply operations, not prose, so there's nothing meaningful to render token by token. A staged progress indicator ("routing → drafting → checking") gives the user the same reassurance during a 30 s worst case, and is honest about which node is running. |
| **WebSockets** | That's Option B. We chose Option A. Note that `websockets` appears in the lockfile as a `langgraph-sdk` transitive dependency — it is not imported by our code. |
| **LangSmith tracing** | Arrives transitively with LangGraph; never enabled. It would send prompts containing patent text to a third party, which is not a decision to make by accident on a legal document. No env var, no client, no opt-in. |
| **Temporal / Prefect** | See §2.5. Durable execution for a sub-minute request. |
| **Redux / MobX** | See §3.1. |
| **Alembic** | See §4.12. |
| **Celery / task queue** | Edits are synchronous. 30 s worst case is inside a normal HTTP timeout, and a job queue would need a result store, polling and a second process. |
| **Redis** | Nothing to cache; no shared state between processes — deliberately, per §4.14. |
| **CI (GitHub Actions)** | Real value in production, none for a zip file. Mentioned in future work. |
| **Auth / SuperTokens** | Out of scope; noted as the integration point with Solve's stack. |

---

## 6. Verification log

Claims in this document were checked by running the real tools, not from memory.

| Claim | Method | Result |
|---|---|---|
| `openai` API surface | Imported `openai==1.109.1` in the project venv | `chat.completions.parse` **exists** (stable); `openai.resources.beta.chat` **does not exist** |
| **Model id valid on the key** | `client.models.retrieve("gpt-5.2-2025-12-11")`, 2026-08-13 | **Valid**, `owned_by=system` |
| **Strict Structured Outputs, live** | 14 real `parse` calls with `response_format=EditPlan` / `Understanding` | Accepted; `to_strict_json_schema` clean on both; **no 400 on any call** |
| **`temperature` on `gpt-5.2-2025-12-11`** | Probed at 0.0, 1.0 and 2.0 | **All three ACCEPTED.** The "reasoning models reject `temperature`" assumption this document carried in §7 was **false for this model** |
| **Reasoning-token consumption** | `usage.completion_tokens_details.reasoning_tokens` on all 14 calls | **0, every time.** Completion tokens 74–129 |
| **Per-call latency** | Wall clock over the same 14 calls | **min 1.1 s · median 1.5 s · max 6.7 s** |
| **Instruction → operation mapping** | 14 real instructions through one planner call each, over Patent 1's outline | **11 correct first try.** Three failures, all traced to our prompts and all fixed by specification changes — PLAN §20.7 |
| `setContent` signature | Read `@tiptap/core@2.27.1` type declarations | Positional `(content, emitUpdate?, parseOptions?, options?)` — we pass `setContent(html, true)`. `errorOnInvalidContent` is available but **deliberately not used** (PLAN §1.1): with no `onContentError` handler it silently drops content from a stored version |
| TipTap round-trip stability | Ran TipTap + StarterKit over the real seed under jsdom | `parse → getHTML → parse → getHTML` **byte-identical** |
| `getHTML()` output shape | Same run | Single line, 0 newlines, whitespace collapsed, `<h1>` kept, `<!DOCTYPE>`/`<head>`/`<title>` **stripped** |
| Seed structure | Same run | Patent 1 → 19 `<p>`, 8 claims, claim 1 spans 5 paragraphs; patent 2 → 9 claims |
| Seed size (RAG relevance) | Measured the stored content | ~2.7 KB — the whole document fits in context; retrieval has nothing to solve |
| StarterKit schema | Read bundled extension list | Confirmed node/mark set in §2.2; **`history` is included**, so native `Cmd+Z` works |
| Backend deps resolve | `uv pip install --dry-run` | `beautifulsoup4==4.15.0`, `nh3==0.3.6`, `pydantic-settings==2.15.0`, `pytest==9.1.1` — clean |
| **LangGraph install cost** | `uv add langgraph` in an isolated copy of `server/`, measuring both ends of the `--no-dev` venv | `langgraph==1.2.11`; **+21 packages**; `uv.lock` **35 → 56**; venv **33 → 54** distributions, **61.5 → 78.5 MiB (+17 MiB)** |
| **LangGraph conflicts** | Inspected the resolved lock after the add | **Zero.** `openai`, `pydantic`, `fastapi`, `sqlalchemy`, `httpx` all unchanged |
| **`interrupt()` semantics** | Ran a graph that interrupts inside a planning node, then resumed | The node **re-executes from the top** on resume — the LLM is called a second time, so the user can approve plan A and receive plan B (§4.14) |
| **`InMemorySaver` across processes** | Resumed a thread from a fresh process | Raises `KeyError: 'instruction'` **inside node execution**, not a clean recoverable error. Combined with `uvicorn --reload` in `server/Dockerfile`, every source edit destroys pending confirmations |
| **`SqliteSaver` availability** | Attempted the import after `uv add langgraph` | **Not bundled** — requires `langgraph-checkpoint-sqlite`, `aiosqlite`, `sqlite-vec` (3 further packages) |
| Frontend deps available | `npm view` | `zustand@5.0.14`, `vitest@4.1.10`, `@testing-library/react@16.3.2`, `jsdom@30.0.1` |
| Client install state | Inspected `client/node_modules` | Complete — 229 packages, `.bin` present |

### Environment note

The local venv runs **Python 3.14.7**; the Dockerfile targets **`python:3.13-slim`**, and
`pyproject.toml` requires `>=3.13`. Both work, but "works locally, differs in Docker" is exactly how
submissions break on a reviewer's machine. **Phase 0 pins local development to 3.13** to match the
image that reviewers actually run.

---

## 7. Open items requiring a live API call — **RESOLVED 2026-08-13**

The pre-flight has been run against the real key (PLAN §20.7). Items 1–3 are closed by measurement;
item 4 is closed in part and is now a manual calibration rather than an unknown.

| # | Question | Measured answer |
|---|---|---|
| 1 | **Does `gpt-5.2-2025-12-11` accept our exact `parse` call?** | **Yes.** `client.models.retrieve` resolves the id (`owned_by=system`); `client.chat.completions.parse` accepts `response_format=` with our Pydantic models on the **stable** path; `to_strict_json_schema` is clean. `temperature` is accepted at 0.0, 1.0 and 2.0 — **but only when `reasoning_effort` is absent; see the correction below, which is the single most expensive thing this document got wrong.** We send `temperature=0` on the deterministic nodes and omit it on the generative ones (PLAN §21.2) |
| 2 | **Is `reasoning_effort` supported, and does `"low"` help latency?** | **Supported, and shipped as `None` anyway.** It cannot help, because **`reasoning_tokens` was 0 on every one of 14 calls** — there is no reasoning pass to shorten on this model — and it is mutually exclusive with the temperatures we do want. See the correction below |

> ### The correction that cost a whole feature — 2026-08-14
>
> Items 1 and 2 above were each measured **in isolation**, and each answer is correct in isolation.
> **The combination was never tried, and the combination is rejected:**
>
> | `reasoning_effort` | `temperature` | Result |
> |---|---|---|
> | `"low"` | omitted | accepted |
> | `"low"` | `1.0` | accepted |
> | absent | `0.0` | accepted |
> | `"low"` | `0.0` | **400 `Unsupported value: 'temperature' does not support 0.0 with this model`** |
>
> The shipped configuration used exactly the failing pair — `openai_reasoning_effort="low"` from
> `config.py`, `temperature=0` on `understand`/`plan_ops`/`judge` from §21.2 — so **three of the
> five nodes returned 400 on every live call and the AI feature did not work at all.** It was found
> by the first manual click-through in Step 6, not by the test suite, because **no test in this
> repository makes a live API call** and 4Z was the only live gate.
>
> **Resolved by defaulting `openai_reasoning_effort` to `None`.** `reasoning_effort` is the one
> dropped, on this document's own evidence: `reasoning_tokens == 0` means it buys nothing
> measurable here, whereas `temperature=0` carries §21.2's deterministic/generative split. Guarded
> by tests **L11** (configuration) and **L12** (the kwargs actually sent), so re-enabling
> `reasoning_effort` without clearing those temperatures now fails in CI rather than in production.
>
> **The lesson is about method, not about OpenAI.** A pre-flight that varies one parameter at a
> time proves one thing about each parameter and nothing about the request you actually send. The
> assertion worth making is over the *shipped call*, which is what L12 now is.
| 3 | **Real end-to-end latency for the worst case** | **Measured: min 1.1 s, median 1.5 s, max 6.7 s per call** (n = 14, real schemas over the seed outline; the 6.7 s outlier carried prior-art text). Five calls at the observed max ≈ 33 s; at the median ≈ 7.5 s. The estimate of ~30 s for the *worst* case was roughly right; the ~5–15 s estimate for a *single* call was an order of magnitude high. The timeout chain is re-derived from this in PLAN §3.4: **12 s per call / 65 s graph deadline / 75 s server / 90 s client** |
| 4 | **Does the judge threshold discriminate?** | **Partly answered, and it moved the rubric.** The live run showed the model refusing a *required* README example because the requested claim duplicated an existing one — so the judge's original "CONTRADICTION OR DUPLICATION" check would have rejected that same edit on every retry. Duplication is now explicitly **not** a failure; the five checks are otherwise unchanged. Whether the remaining rubric over- or under-fires is still a manual calibration against the seed claims |

Items 1–3 were isolated inside `llm.py` and cost no design change beyond the numbers above. Item 4
is prompt text in `prompts.py`. **The one thing this exercise proved about the plan's structure is
that it was right to put every network unknown behind one module: four assumptions were overturned —
three at 4Z and the mutual exclusion above at Step 6 — and no engine module changed. The fix for a
broken feature was one default value in `config.py`.**

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
| AI pipeline | **langgraph 1.2** — understand, retrieve, planner/drafter, LLM judge with a bounded retry cycle, deterministic verify. **No checkpointer**; HITL between two runs |
| Correctness | deterministic `verify()` over the applied document — numbering, cross-references, empty claims |
| Tests | **pytest** (backend) + **vitest** & Testing Library (frontend) |
| Lint | **ruff** (Python) + ESLint 9 flat config (TS) |
| Ops | Docker Compose *(inherited)* |

**Six new runtime dependencies** — `beautifulsoup4`, `nh3`, `pydantic-settings`, `langgraph`,
`zustand`, and finally using `openai` — plus test and lint tooling.

Five of those six are near-free. **`langgraph` is not: it is 21 transitive packages, `uv.lock` 35 →
56, and 17 MiB of venv.** It is here because the pipeline contains a real cycle — an LLM judge that
sends unsatisfactory claim text back to the drafter, at most twice — and a cycle with a retry budget
is the one control-flow shape that `if/elif` genuinely cannot express honestly. We take the graph
runtime and decline the rest of the ecosystem: no chains, no agents, no retrievers, no memory, no
checkpointer, no tracing.

The bill in full, stated rather than hidden: **21 new packages, 3–5 LLM calls on a generative
request, ~7.5 s at the measured median and ~33 s at the observed per-call worst case when the judge
asks for a retry** (§7). Mechanical edits — the majority, and the fast-path ones — remain a single
call or none.

The through-line is unchanged: **the LLM is used for language, and only language.** Structure,
numbering, ordering, validation, consent and persistence are deterministic Python. The graph
schedules those steps; it never decides them. That is what makes this testable, explainable, and
safe to run on a legal document.
