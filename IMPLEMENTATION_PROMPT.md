# Implementation session prompt

One fresh agent session per row. Replace `{{STEP}}` and `{{SECTION}}` in the template below.

| # | `{{STEP}}` | `{{SECTION}}` | Notes |
|---|---|---|---|
| 1 | `0A + 0B` | §4–§5 | 0B's gate builds what 0A locked |
| 2 | `0C` | §6 | independent of 0A/0B |
| 3 | `1A + 1B + 1C` | §7–§9 | **must be one session — one commit (C5)** |
| 4 | `2A` | §10 | |
| 5 | `2B` | §11 | |
| 6 | `2C` | §12 | |
| 7 | `2D` | §13 | **Task 1 demoable** — stop and demo here |
| 8 | `3A` | §14 | |
| 9 | `3B` | §15 | |
| 10 | `4A + 3C` | §17 + §16 | 3C imports `Op` from 4A — **do 4A first** |
| 11 | `4B + 4C` | §18–§19 | |
| 12 | `5A` | §20 | |
| 13 | `5B` | §21 | **Option A demoable** |
| 14 | `6` | §22 | hardening — checklist, no gate |
| 15 | `7` | §23 | docs + submission |

---

## The template

````
You are an expert-level senior engineer implementing ONE step of an agreed plan.

## Your assignment
Implement **step {{STEP}}** of `PLAN.md`, specified in **{{SECTION}}**. Nothing else.

## Read first, in this order — all of them, in full
1. `PLAN.md` {{SECTION}} — your specification. It is detailed and decided; follow it.
2. `PLAN.md` §1 (scope cuts) and §2 (corrections C1–C35) — these are binding decisions taken
   after investigation. Several are runtime-fatal if ignored. Do not re-litigate them.
3. `PLAN.md` §3 — the phase map, so you know what already exists and what comes later.
4. `CLAUDE.md` — the 9 architecture invariants and the code style. These override defaults.
5. The existing code in the areas you will touch.

## Scope discipline — this is the most important instruction
- Implement **only** step {{STEP}}. Do not start the next step, however small or tempting.
- **The plan deliberately defers things.** If something looks missing, incomplete, or stubbed,
  check §3 and the later sections BEFORE "fixing" it — it is very likely another step's job.
  Examples of intentional deferrals: §13 renders a `"Chat — added in §21"` placeholder; §6
  creates `setup.ts` as a stub that §11 appends to; §12 makes a deliberately minimal `App.tsx`
  edit because §13 owns the rewrite.
- If you genuinely believe the plan is wrong, **stop and say so with evidence** rather than
  silently deviating. A plan defect is a finding, not something to route around.
- Do not add features, dependencies, abstractions, or "while I'm here" refactors. `CHALLENGE.md`
  asks for code that is not overly complex, and the next round is pair programming without AI —
  every file must be explainable in under a minute.

## Definition of done
1. Every file in the step's Files list exists with the specified content.
2. **Every test in the step's exit gate is written and passing** — by the exact test IDs listed.
   Tests must genuinely assert what their name claims. A test that passes trivially is a defect.
3. The full suite is green, not just your new tests:
   - backend: `cd server && uv run pytest && uv run ruff check .`
   - frontend: `cd client && npm run lint && npm run test && npm run build`
   Run whichever apply to your step; run both if you touched both.
4. Every checkbox in the step's exit gate is satisfied, including the manual/command ones.
5. The tree is left green. Never "green after the next step."

## Verify like an engineer, not like a checklist
- Actually run things. Do not report a command as passing without running it.
- Exercise real and edge cases relevant to your step: empty content (`""` is legal and falsy —
  a real trap), missing rows, oversized payloads, malformed HTML, fast repeated clicks, absent
  API key. If an end-to-end check through the running app (`docker-compose up`, or the dev
  servers) is the only honest way to confirm something works, do it.
- Where the plan flags a trap, prove you avoided it. Examples: no `server/data/app.db` after a
  test run; no second Vite in `node_modules`; the app starts with no `OPENAI_API_KEY`.

## Then: independent verification
When you believe the step is complete, **spawn a separate expert-level reviewer agent** and give
it: the step, {{SECTION}}, your diff, and your test output. Instruct it to:
- verify every exit-gate item is genuinely met, by re-running the commands itself;
- confirm each test asserts what its name claims and would fail if the behaviour regressed;
- check the relevant `CLAUDE.md` invariants are respected;
- confirm **no work from other steps leaked in**, and nothing the step owns was skipped;
- hunt for gaps, wrong assumptions and silent failures — and be blunt; finding nothing is a
  failed review.

**If the reviewer finds gaps in YOUR step, fix them and re-verify** — loop until clean. If it
finds a gap that `PLAN.md` assigns to a later step, do not fix it; note it in your report.

## Report back
- What you implemented, file by file.
- Test results as actual output — including anything that failed on the way and how you fixed it.
- The reviewer's findings and what you did about each.
- Anything deferred to a later step, and where the plan assigns it.
- Any point where the plan was wrong, ambiguous, or under-specified — quote it, and say what you
  did. Be honest about what is not done; a known gap reported is far better than a silent one.
````

---

## Notes for whoever runs these

- **Run them in order.** Each step's entry criteria assume the previous gate is green.
- **Commit per session**, message naming the step.
- **Stop and demo after session 7 (2D)** — that is Task 1 complete. If time runs short, a
  finished Task 1 is worth more than a half-built Task 2.
- **Session 11 (4B+4C) needs the OpenAI key** for its manual check. Everything before it, and the
  whole engine, runs offline by design — so a missing key blocks only that one manual step, not
  the build.
