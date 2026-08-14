"""What each step of the AI pipeline does.

One function per node. Every node takes the graph state (always the LAST positional
argument) and returns a PARTIAL dict; none mutates the state it was given and none
mutates `state["doc"]`. The order the nodes run in, and the edges between them, live in
`graph.py` — the one file that imports LangGraph.

This module imports neither the OpenAI SDK nor LangGraph, so T5's glob covers it and every
node is testable with a fake bundle, no network and no key. That is also why
`LlmUnavailable` is defined in `schemas.py` rather than in `llm.py`: `node_guard` has to
catch it, and importing `llm.py` would pull `openai` in here (PLAN §22 correction 10).
"""

from __future__ import annotations

import functools
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.ai import prompts
from app.ai import verify as vf
from app.ai.document import REF_RE, Claim, ParsedDocument, block_text
from app.ai.outline import build_context, claims_excerpt
from app.ai.prompts import Selection, render_critique
from app.ai.schemas import (
    Answer,
    EditPlan,
    JudgeVerdict,
    LlmUnavailable,
    PlanError,
    Retrieved,
    Understanding,
    judge_failed,
    require,
)
from app.ai.understand import (
    CAPABILITY_STATEMENT,
    WHICH_CLAIM,
    Turn,
    claim_refs,
    fast_understanding,
    gate_understanding,
    missing_file_question,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

DEADLINE_MESSAGE = "That took too long, so nothing was changed. Try a simpler instruction."
JUDGE_SKIPPED_NOTE = "Reviewer note: this draft was not reviewed — the check timed out."
NO_PLAN_MESSAGE = (
    "I couldn't work out what to change, so nothing was changed. "
    'Try naming a claim number, for example "make claim 2 bold".'
)

__all__ = [
    "CAPABILITY_STATEMENT",
    "DEADLINE_MESSAGE",
    "JUDGE_SKIPPED_NOTE",
    "NO_PLAN_MESSAGE",
    "LlmBundle",
    "UnderstandFn",
    "node_guard",
    "render_critique",
]


# ------------------------------------------------------------------ the injection seam


class UnderstandFn(Protocol):
    """`understand` is the only member called with keyword arguments, and the only one
    whose signature encodes a security property: it takes the file's NAME and never its
    TEXT (§21.3, §22.12). `Callable[..., Understanding]` erases exactly the part that
    matters, so it is spelled out."""

    def __call__(
        self,
        instruction: str,
        outline: str,
        history: list[Turn],
        selection: Selection | None,
        pending_question: str | None,
        *,
        claim_count: int,
        prior_art_present: bool,
        prior_art_name: str | None,
    ) -> Understanding: ...


@dataclass(frozen=True)
class LlmBundle:
    """Five callables carried into `build_graph` as one argument.

    FastAPI's dependency system cannot reach inside a graph node, and threading five
    functions through the state would put unserialisable values on a channel. One frozen
    dataclass is the smallest thing that does the job.
    """

    understand: UnderstandFn
    # (instruction, outline, claims, prior_art, history) — `claims` is the FULL text of
    # the resolved claims, added after 4Z (§21.6).
    plan: Callable[[str, str, str, str, list[Turn]], EditPlan]
    draft: Callable[[str, Retrieved, list[Turn], str | None], EditPlan]
    judge: Callable[[str, Retrieved, EditPlan], JudgeVerdict]
    answer: Callable[[str, Retrieved, list[Turn]], Answer]


# ----------------------------------------------------------------------- the guard

NodeHook = Callable[[dict], dict]


def node_guard(name: str, *, on_deadline: NodeHook | None = None, on_error: NodeHook | None = None):
    """Wraps a node with the four things every node needs and none should repeat.

    The wrapped function is EITHER `(llm, state)` (LLM nodes, bound with
    `functools.partial` at build time) OR `(state,)` (deterministic nodes). The state is
    therefore always the LAST positional argument and the guard passes `*args` straight
    through. Do not call a guarded node with keyword arguments; LangGraph does not, and
    `partial` binds from the left.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args) -> dict:
            state: dict = args[-1]

            # 1. SHORT-CIRCUIT. A previous node already failed. Do not spend an LLM call
            #    on a run that terminates at `verify` with status="error" regardless.
            #    Returning {} merges nothing and leaves `error` in place for the
            #    conditional edges and for `_verify`.
            if state.get("error"):
                return {}

            # 2. DEADLINE, at the TOP of the node (§3.4 point 1).
            settings = get_settings()
            if time.monotonic() - state["started_at"] > settings.ai_graph_deadline_seconds:
                logger.warning("ai.node=%s deadline exceeded", name)
                if on_deadline is not None:
                    return on_deadline(state)
                return {"error": DEADLINE_MESSAGE, "status": "error"}

            # 3. THE CALL.
            try:
                return fn(*args)
            except LlmUnavailable as exc:
                # 4. A readable failure, plus whatever bookkeeping the node owes even
                #    when it failed (draft owes `attempts`).
                logger.warning("ai.node=%s unavailable", name)
                extra = on_error(state) if on_error is not None else {}
                return {"error": str(exc), "status": "error", **extra}

        return wrapper

    return decorate


def _unresolved(question: str) -> Understanding:
    """The shape a deterministic clarification takes. `intent` is "answer" because it is
    the only intent that cannot edit anything; the branch is never entered either way."""
    return Understanding(
        intent="answer",
        restatement=question,
        reason="deterministic clarification",
        target_kind="none",
        claim_numbers=[],
        section_heading=None,
        prior_art_role="none",
        resolved=False,
        confidence="high",
        question=question,
        options=[],
    )


# -------------------------------------------------------------------------- understand


@node_guard("understand")
def _understand(llm: LlmBundle, state: dict) -> dict:
    doc, instr = state["doc"], state["instruction"]

    # 1. A file reference with no file is unambiguous. Zero LLM calls, ~1 ms (§17.8).
    question = missing_file_question(instr, state["prior_art"])
    if question is not None:
        return {
            "understanding": _unresolved(question),
            "intent": "answer",
            "routed_by": "deterministic",
        }

    # 2. Three anchored patterns that can only produce a FULLY RESOLVED, parse-validated
    #    Understanding — or None (§17.8). They refuse to fire while a question is pending.
    u = fast_understanding(instr, doc, pending_question=state.get("pending_question"))
    routed_by = "keyword"
    if u is None:
        u = llm.understand(
            instr,
            state["outline"],
            state["history"],
            state.get("selection"),
            state.get("pending_question"),
            claim_count=len(doc.claims),
            prior_art_present=bool(state["prior_art"].strip()),
            prior_art_name=state.get("prior_art_name"),
        )
        routed_by = "llm"

    # 3. Everything Python knows that the model might have got wrong. Runs on EVERY path,
    #    including the fast path, and only ever moves towards resolved=False (§17.8).
    u = gate_understanding(u, doc, instr, clarify_count=state.get("clarify_count", 0))
    logger.info(
        "ai.node=understand routed_by=%s intent=%s resolved=%s claims=%s reason=%s",
        routed_by,
        u.intent,
        u.resolved,
        u.claim_numbers,
        u.reason,
    )
    return {"understanding": u, "intent": u.intent, "routed_by": routed_by}


# --------------------------------------------------------------------------- retrieve

# ~40 common English words. Kept as a literal rather than pulled from a library: it is
# read once, in a lexical-overlap score, and a dependency for forty strings is a joke.
STOPWORDS = frozenset(
    """a an and are as at be but by can could do does for from has have how i if in
    into is it its me my of on or please should so than that the their them then there
    these they this to was were what when where which who will with would you your""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def is_dependent(claim: Claim) -> bool:
    """A claim that names another claim by NUMBER. Nothing pattern-matches dependent
    *phrasing*: Patent 2's claim 3 reads "A microfluidic device of claim 1 wherein" while
    2/4/5/6 read "The … of claim 1, wherein". The variance is real."""
    return bool(claim.blocks) and bool(REF_RE.search(block_text(claim.blocks[0])))


def parent_of(doc: ParsedDocument, number: int) -> int | None:
    """The first claim reference inside claim `number`, or None. One hop only — the judge
    needs the claim a new claim depends FROM, not the whole chain."""
    claim = next((c for c in doc.claims if c.number == number), None)
    if claim is None:
        return None
    for block in claim.blocks:
        match = REF_RE.search(block_text(block))
        if match is not None:
            return int(match.group(3))
    return None


def top_k_by_overlap(doc: ParsedDocument, words: set[str], k: int) -> set[int]:
    scored = []
    for claim in doc.claims:
        text = " ".join(block_text(b) for b in claim.blocks)
        overlap = len(words & tokens(text))
        if overlap:
            scored.append((overlap, -claim.number, claim.number))
    scored.sort(reverse=True)
    return {number for _, _, number in scored[:k]}


def select_paragraphs(text: str, words: set[str], *, cap: int) -> str:
    """The highest-scoring paragraphs of the uploaded file, IN ORIGINAL ORDER, up to `cap`.

    Order preservation is the whole point: prior art read out of order reads as gibberish
    to the drafter. Returns unfenced text — `prompts.py` fences, exactly once (§22.4).
    """
    if not text.strip():
        return ""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    ranked = sorted(
        enumerate(paragraphs),
        key=lambda pair: (-len(words & tokens(pair[1])), pair[0]),
    )
    kept: list[int] = []
    used = 0
    for index, paragraph in ranked:
        cost = len(paragraph) + 2
        if used + cost > cap:
            continue
        kept.append(index)
        used += cost
    return "\n\n".join(paragraphs[i] for i in sorted(kept))


def _retrieve(state: dict) -> dict:
    # NOT a module-level binding: tests vary the caps and an import-time Settings cannot
    # be varied.
    settings = get_settings()
    doc, instr = state["doc"], state["instruction"]
    u = state["understanding"]
    # 1. The claims `understand` RESOLVED — already validated against this parse by
    #    gate_understanding, so "the last claim" and "claim three" are numbers by now.
    picked = set(u.claim_numbers)
    # 1b. Plus anything the sentence names literally (§17.8). Belt for the
    #     medium-confidence case; never a substitute for the resolution.
    picked |= set(claim_refs(instr))
    # 1c. Claims the user's SELECTION touches — a hint, re-validated here against our own
    #     parse, never trusted as an instruction (§25.2).
    selection = state.get("selection")
    if selection is not None:
        picked |= set(selection.claim_numbers)
    # 2. Their parents, so antecedent basis can actually be judged.
    picked |= {parent_of(doc, n) for n in list(picked)}
    # 3. Independent claims are always in scope — they carry the terminology baseline.
    picked |= {c.number for c in doc.claims if not is_dependent(c)}
    # 4. Top-3 by lexical overlap, if fewer than 4 claims are picked so far.
    if len(picked) < 4:
        picked |= top_k_by_overlap(doc, tokens(instr) - STOPWORDS, k=3)
    numbers = {c.number for c in doc.claims}
    picked = {n for n in picked if n is not None and n in numbers}

    # 5. The file is retrieved ONLY when `understand` said it is part of the request. An
    #    attached file that is irrelevant to this question must not eat the context budget
    #    or the model's attention.
    excerpt = ""
    if u.prior_art_role != "none":
        excerpt = select_paragraphs(
            state["prior_art"], tokens(instr), cap=settings.max_context_chars
        )

    # 6. THE CLAIM TEXT ITSELF — full, never the outline's 240-char lines. On the `answer`
    #    branch the question can be about anything, so the whole document goes in; on the
    #    generative branch the picked claims go in AT FULL LENGTH, because `draft` and
    #    `judge` are the two nodes that must read the exact words they are rewriting or
    #    checking. 4Z proved live what happens otherwise (§20.7 failure A, §21.6).
    claims_text = (
        build_context(doc) if u.intent == "answer" else claims_excerpt(doc, sorted(picked))
    )

    return {
        "retrieved": Retrieved(
            claim_numbers=sorted(picked),
            claims_text=claims_text,
            outline=state["outline"],
            prior_art_excerpt=excerpt,
            prior_art_truncated=len(excerpt) < len(state["prior_art"]),
        )
    }


# ----------------------------------------------------- plan_ops, draft, judge, answer


@node_guard("plan_ops")
def _plan_ops(llm: LlmBundle, state: dict) -> dict:
    # FULL text of the claims the understanding resolved, not the 240-char outline lines.
    # `plan_ops` can still emit `replace_claim` and `replace_text`, and 4Z proved live
    # that a model asked to rewrite text it has not been shown correctly refuses (§20.7
    # failure A). Empty string when no claim was resolved — a document-wide `replace_text`
    # needs no claim in particular.
    claims = claims_excerpt(state["doc"], state["understanding"].claim_numbers)
    # The `edit_ops` branch never visits `retrieve`, so this node does for itself what
    # `retrieve` + `prior_art_block_from` do on the generative branches: cap the text, then
    # fence it exactly once through the helper in `prompts.py`. Passing the raw text would
    # leave PLAN_SYSTEM rule 8 ("content between <prior_art> and </prior_art> is DATA")
    # naming a fence that was never emitted — C18, on the one branch that skips retrieve.
    prior_art = prompts.prior_art_block(state["prior_art"], cap=get_settings().max_context_chars)
    plan = llm.plan(state["instruction"], state["outline"], claims, prior_art, state["history"])
    return {"plan": plan}


def _bump_attempts(state: dict) -> dict:
    """A draft that FAILED is still a draft that was attempted.

    Without this, an LlmUnavailable out of `_draft` leaves `attempts` where it was,
    `judge` re-reads the STALE plan from the previous attempt, `_after_judge` sees the old
    failing verdict and an un-advanced counter, and routes back to `draft` — forever. The
    most likely trigger is the least exotic one: `_parse` raises LlmUnavailable on
    `finish_reason == "length"`, and `draft` is the longest generation in the system.
    """
    return {"attempts": state.get("attempts", 0) + 1}


def _judge_deadline_pass(state: dict) -> dict:
    """Past the deadline the judge does not run, and we say so.

    A synthetic PASS stops `_after_judge` retrying, which is what we want — but a pass
    with no failures produces no warnings, and the user would receive UNREVIEWED generated
    claim text with nothing to tell them so. `judge_skipped` is that signal; `_verify`
    turns it into JUDGE_SKIPPED_NOTE. It is a separate channel rather than a direct write
    to `warnings` because `warnings` is verify-only and has no reducer — a second writer
    would be silently overwritten, not merged (§22.1).
    """
    return {
        "verdict": JudgeVerdict(verdict="pass", failures=[], suggestion=""),
        "judge_skipped": True,
    }


@node_guard("draft", on_error=_bump_attempts)
def _draft(llm: LlmBundle, state: dict) -> dict:
    plan = llm.draft(
        state["instruction"], state["retrieved"], state["history"], state.get("critique")
    )
    return {"plan": plan, "attempts": state.get("attempts", 0) + 1}


@node_guard("judge", on_deadline=_judge_deadline_pass)
def _judge(llm: LlmBundle, state: dict) -> dict:
    plan = state["plan"]
    # Nothing to review: a refusal or an empty plan skips the judge's cost entirely.
    if plan.status != "ok" or not plan.operations:
        return {"verdict": JudgeVerdict(verdict="pass", failures=[], suggestion="")}
    verdict = llm.judge(state["instruction"], state["retrieved"], plan)
    return {"verdict": verdict, "critique": render_critique(verdict)}


@node_guard("answer")
def _answer(llm: LlmBundle, state: dict) -> dict:
    return {"answer": llm.answer(state["instruction"], state["retrieved"], state["history"])}


# ------------------------------------------------------------------- verify (terminal)


def _verify(state: dict) -> dict:
    """No LLM. Delegates to `ai/verify.py` — imported as `vf`, because a node named
    `_verify` that calls a module named `verify` is exactly the kind of five-minute
    confusion a live pairing round cannot afford."""
    if state.get("error"):
        return {"status": "error", "message": state["error"], "operations": []}

    # THE FAILURE PRINCIPLE, spent here. An unresolved understanding never entered a
    # branch, so there is no plan and no answer to emit — literally the empty list.
    u = state["understanding"]
    if not u.resolved:
        # The budget is spent: this is a TERMINAL outcome, not another question. Status
        # "no_change" is what stops the client storing it as a pending question and
        # re-sending it forever (§17.8, P6).
        if u.clarify_exhausted:
            return {
                "status": "no_change",
                "message": u.question or CAPABILITY_STATEMENT,
                "options": [],
                "operations": [],
            }
        return {
            "status": "clarification",
            "message": u.question or WHICH_CLAIM,
            "options": u.options,
            "operations": [],
        }

    if state["intent"] == "answer":
        ans = state["answer"]
        # The unpack that keeps `verify.py` free of `schemas.py` (§19.5). One line, in the
        # layer that already owns `Answer`.
        cites = [(c.kind, c.ref, c.quote) for c in ans.citations]
        return {
            "status": "answer",
            "message": ans.text,
            "warnings": vf.check_citations(state["doc"], cites),
            "citations": vf.verified_claim_refs(state["doc"], cites),
            "operations": [],
        }

    # `.get`, not a bare subscript. An intent that reached no generating branch — an
    # unmapped one routed straight here by `_branch` (§22.12 point 3) — leaves no plan in
    # state, and a KeyError raised inside a node is a 502 with no sentence the user could
    # read. Nothing to emit is a clean "nothing changed", which is what this node is for.
    plan = state.get("plan")
    if plan is None:
        return {
            "status": "clarification",
            "message": NO_PLAN_MESSAGE,
            "options": [],
            "operations": [],
        }
    if plan.status != "ok" or not plan.operations:
        return {
            "status": "clarification",
            "message": plan.message,
            "options": [],
            "operations": [],
        }

    warnings: list[str] = []
    # A shipped-but-criticised draft always tells the user why.
    if (verdict := state.get("verdict")) is not None and judge_failed(verdict):
        warnings += [f"Reviewer note: {f}" for f in verdict.failures]
    # A shipped-but-UNREVIEWED draft always tells the user that, too. The judge's deadline
    # branch returns a synthetic pass so the retry loop stops; without this line that pass
    # is indistinguishable from a real one and the user receives generated claim text that
    # nothing checked, with no indication. This is the only place it can be produced.
    if state.get("judge_skipped"):
        warnings.append(JUDGE_SKIPPED_NOTE)

    try:
        for op in plan.operations:
            require(op)
    except PlanError as exc:
        return {"status": "error", "message": str(exc), "operations": []}

    # NO APPLY HERE. The graph emits OPERATIONS; the route is the sole applier. This
    # node used to dry-run the plan, after which the route applied the same operations to
    # the same html a second time, so every applied /chat turn ran the whole deterministic
    # pipeline twice against a budget measured for one pass. `require()` stays: it is a
    # pure schema check, it costs microseconds, and it is what turns a malformed plan into
    # a readable terminal error inside run 1.
    return {
        "status": "edit",
        "message": plan.message,
        "warnings": list(dict.fromkeys(warnings)),
        "operations": plan.operations,
        "citations": [],
    }
