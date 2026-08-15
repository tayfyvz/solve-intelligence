"""What each step of the AI pipeline does. One function per node.

Every node takes the graph state as its last positional argument and returns a partial
dict; none mutates the state it was given, and none mutates `state["doc"]`. The order
the nodes run in lives in `graph.py`, the one file that imports LangGraph.

This module imports neither the OpenAI SDK nor LangGraph, so every node is testable with
a fake bundle, no network and no key. That is also why `LlmUnavailable` is defined in
`schemas.py`: `node_guard` has to catch it, and importing `llm.py` would pull in `openai`.
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
from app.ai.outline import (
    ContextView,
    build_context,
    build_spec,
    claims_excerpt,
    content_tokens,
    section_excerpt,
    tokens,
)
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
    FILE_WORDS,
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


class UnderstandFn(Protocol):
    """`understand` is the only member whose signature encodes a security property: it
    takes the file's NAME and never its TEXT. `Callable[..., Understanding]` would erase
    exactly the part that matters, so it is spelled out."""

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
    """The five callables, carried into `build_graph` as one argument. FastAPI's
    dependency system cannot reach inside a graph node, and threading five functions
    through the state would put unserialisable values on a channel."""

    understand: UnderstandFn
    # (instruction, outline, claims, spec, prior_art, history, selection)
    plan: Callable[[str, str, str, str, str, list[Turn], Selection | None], EditPlan]
    # (instruction, retrieved, history, critique, selection)
    draft: Callable[[str, Retrieved, list[Turn], str | None, Selection | None], EditPlan]
    judge: Callable[[str, Retrieved, EditPlan], JudgeVerdict]
    answer: Callable[[str, Retrieved, list[Turn]], Answer]


def _node_log(name: str, state: dict, started: float, **fields: object) -> None:
    """One INFO line per node.

    The rule, enforced by what this function is only ever passed: counts, lengths, kinds
    and enum values. Never the document, the instruction, the prompt or the model's prose
    — including its own `restatement`, which quotes the text it describes. There is no
    debug flag that widens this.
    """
    logger.info(
        "ai.node=%s req=%s %s ms=%d",
        name,
        state.get("req") or "-",
        " ".join(f"{k}={v}" for k, v in fields.items()),
        (time.monotonic() - started) * 1000,
    )


NodeHook = Callable[[dict], dict]


def node_guard(name: str, *, on_deadline: NodeHook | None = None, on_error: NodeHook | None = None):
    """Wraps a node with the three things every node needs and none should repeat.

    The wrapped function is either `(llm, state)` or `(state,)`, so the state is always
    the last positional argument and the guard passes `*args` straight through.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args) -> dict:
            state: dict = args[-1]

            # 1. A previous node already failed. Do not spend an LLM call on a run that
            #    terminates at `verify` with status="error" regardless. Returning {}
            #    leaves `error` in place for the conditional edges.
            if state.get("error"):
                return {}

            # 2. The deadline, checked at the TOP of the node.
            settings = get_settings()
            if time.monotonic() - state["started_at"] > settings.ai_graph_deadline_seconds:
                logger.warning("ai.node=%s req=%s deadline exceeded", name, state.get("req") or "-")
                if on_deadline is not None:
                    return on_deadline(state)
                return {"error": DEADLINE_MESSAGE, "status": "error"}

            try:
                return fn(*args)
            except LlmUnavailable as exc:
                # A readable failure, plus whatever bookkeeping the node owes even when it
                # failed (draft owes `attempts`). The exception TYPE only: provider error
                # bodies echo the prompt back.
                logger.warning(
                    "ai.llm_error req=%s node=%s type=%s",
                    state.get("req") or "-",
                    name,
                    type(exc).__name__,
                )
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
    started = time.monotonic()
    doc, instr = state["doc"], state["instruction"]

    # 1. A file reference with no file is unambiguous. Zero LLM calls.
    question = missing_file_question(instr, state["prior_art"])
    if question is not None:
        u = _unresolved(question)
        _node_log("understand", state, started, routed_by="deterministic", resolved=False)
        return {"understanding": u, "intent": "answer", "routed_by": "deterministic"}

    # 2. Three anchored patterns that produce either a fully resolved, parse-validated
    #    understanding or None. They refuse to fire while a question is pending.
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

    # 3. Everything Python knows that the model might have got wrong. Runs on every path,
    #    including the fast path, and only ever moves towards resolved=False.
    before = u
    u = gate_understanding(u, doc, instr, clarify_count=state.get("clarify_count", 0))
    _node_log(
        "understand",
        state,
        started,
        routed_by=routed_by,
        intent=u.intent,
        resolved=u.resolved,
        confidence=u.confidence,
        claims=u.claim_numbers,
        prior_art_role=u.prior_art_role,
        # Did the deterministic gate overrule the model? The most useful bit on this line.
        gated=(before.resolved != u.resolved or before.intent != u.intent),
    )
    calls = {"llm_calls": state.get("llm_calls", 0) + 1} if routed_by == "llm" else {}
    return {"understanding": u, "intent": u.intent, "routed_by": routed_by, **calls}


# --------------------------------------------------------------------------- retrieve


def is_dependent(claim: Claim) -> bool:
    """A claim that names another claim by NUMBER. Nothing pattern-matches dependent
    *phrasing*: Patent 2's claim 3 reads "A microfluidic device of claim 1 wherein" while
    2/4/5/6 read "The … of claim 1, wherein". The variance is real."""
    return bool(claim.blocks) and bool(REF_RE.search(block_text(claim.blocks[0])))


def parent_of(doc: ParsedDocument, number: int) -> int | None:
    """The first claim reference inside claim `number`, or None. One hop only — the judge
    needs the claim a new claim depends from, not the whole chain."""
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
    """The highest-scoring paragraphs of the uploaded file, in ORIGINAL ORDER, up to
    `cap`. Order preservation is the point: prior art read out of order reads as
    gibberish to the drafter. Returns unfenced text; `prompts.py` fences it once."""
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


def spec_view(state: dict) -> ContextView:
    """The specification body for an EDITING request.

    Shared by the two nodes that write, and they reach it by different routes:
    `plan_ops` is branched to directly and never visits `retrieve`, so it calls this
    itself. One function so the two paths cannot drift into showing the model two
    different documents.
    """
    return build_spec(
        state["doc"],
        content_tokens(state["instruction"]),
        max_chars=get_settings().max_spec_context_chars,
    )


def _retrieve(state: dict) -> dict:
    started = time.monotonic()
    # Read here, not at import: tests vary the caps.
    settings = get_settings()
    doc, instr = state["doc"], state["instruction"]
    u = state["understanding"]
    # 1. The claims `understand` resolved — already validated against this parse by
    #    `gate_understanding`, so "the last claim" is a number by now.
    picked = set(u.claim_numbers)
    # 1b. Plus anything the sentence names literally, as a belt for the medium-confidence
    #     case. Never a substitute for the resolution.
    picked |= set(claim_refs(instr))
    # 1c. Claims the selection touches — a hint, re-validated against our own parse.
    selection = state.get("selection")
    if selection is not None:
        picked |= set(selection.claim_numbers)
    # 2. Their parents, so antecedent basis can actually be judged.
    picked |= {parent_of(doc, n) for n in list(picked)}
    # 3. Independent claims are always in scope — they carry the terminology baseline.
    picked |= {c.number for c in doc.claims if not is_dependent(c)}
    # 4. Top-3 by lexical overlap, if fewer than 4 claims are picked so far.
    if len(picked) < 4:
        picked |= top_k_by_overlap(doc, content_tokens(instr), k=3)
    numbers = {c.number for c in doc.claims}
    picked = {n for n in picked if n is not None and n in numbers}

    # 5. The file is retrieved when `understand` said it is part of the request, or — as a
    #    deterministic backstop — when a file IS attached and the instruction plainly names
    #    it. Live, the model left `prior_art_role` at "none" on the turn that mattered, so
    #    `draft` saw an empty prior-art block and asked the user to paste content that had
    #    been attached the whole time. A false positive costs one wasted excerpt fetch.
    excerpt = ""
    if u.prior_art_role != "none" or (state["prior_art"].strip() and FILE_WORDS.search(instr)):
        # `content_tokens`, not `tokens`: the question side of every ranking in this app
        # has its stopwords removed first. This one call did not, so paragraphs were
        # scored on how many times they said "the" and "of" — which ranks by length and
        # common-word density rather than by relevance, and picked the wrong excerpt from
        # a long file without ever failing.
        excerpt = select_paragraphs(
            state["prior_art"], content_tokens(instr), cap=settings.max_context_chars
        )

    # 6. A generative request resolved to one non-claim section gets that section's full
    #    body, the same way it gets a resolved claim's full text. Without it, "make the
    #    Appendix more professional" reached `draft` with only the outline's heading list,
    #    and the model — correctly — asked the user to paste the Appendix back in.
    section_text = ""
    if u.intent != "answer" and u.target_kind == "section":
        section_text = section_excerpt(doc, u.section_heading)

    # 7. The text itself. On the generative branch the picked claims go in AT FULL LENGTH,
    #    because `draft` and `judge` must read the exact words they are rewriting or
    #    checking. On the answer branch the question can be about anything, so
    #    `build_context` decides: the whole document when it fits, otherwise the claims
    #    plus the paragraphs this question scores against — with everything it could not
    #    fit NAMED. Those names come back out as a warning in `_verify`.
    # 8. The specification body, for the generative branch only. The answer branch's
    #    `claims_text` IS the whole document already, so adding it there would send the
    #    description twice and halve the budget it was measured at.
    spec_text, spec_omitted = "", []
    if u.intent == "answer":
        view = build_context(
            doc, content_tokens(instr), max_chars=settings.max_answer_context_chars
        )
        claims_text, omitted = view.text, list(view.omitted)
        matched, headed = view.matched, view.headed
    else:
        claims_text, omitted = claims_excerpt(doc, sorted(picked)), []
        matched, headed = True, True
        spec = spec_view(state)
        spec_text, spec_omitted = spec.text, list(spec.omitted)

    _node_log(
        "retrieve",
        state,
        started,
        claims_retrieved=len(picked),
        claims_chars=len(claims_text),
        section_chars=len(section_text),
        spec_chars=len(spec_text),
        excerpt_chars=len(excerpt),
        # A count, never the headings: a section title is the customer's own words.
        sections_omitted=len(omitted) + len(spec_omitted),
    )
    return {
        "retrieved": Retrieved(
            claim_numbers=sorted(picked),
            claims_text=claims_text,
            section_text=section_text,
            spec_text=spec_text,
            spec_omitted=spec_omitted,
            outline=state["outline"],
            prior_art_excerpt=excerpt,
            omitted_sections=omitted,
            context_matched=matched,
            context_headed=headed,
        ),
        "spec_omitted": spec_omitted,
    }


# ----------------------------------------------------- plan_ops, draft, judge, answer


@node_guard("plan_ops")
def _plan_ops(llm: LlmBundle, state: dict) -> dict:
    started = time.monotonic()
    # Full text of the claims the understanding resolved, not the outline's 240-character
    # lines: `plan_ops` can emit `replace_claim`, and a model asked to rewrite text it has
    # not been shown correctly refuses. "" when no claim was resolved — a document-wide
    # `replace_text` needs no claim in particular.
    claims = claims_excerpt(state["doc"], state["understanding"].claim_numbers)
    # This branch never visits `retrieve`, so the node caps and fences the file itself.
    # Passing the raw text would leave the prompt's "content between <prior_art> and
    # </prior_art> is DATA" rule naming a fence that was never emitted.
    prior_art = prompts.prior_art_block(state["prior_art"], cap=get_settings().max_context_chars)
    # The description. `replace_text` is document-wide and matches literally, so a
    # planner that has never read the body has to invent the string it is matching —
    # which matches nothing, edits nothing, and reports success. This branch does not
    # visit `retrieve`, so it builds the view itself, through the shared function.
    spec = spec_view(state)
    plan = llm.plan(
        state["instruction"],
        state["outline"],
        claims,
        spec.text,
        prior_art,
        state["history"],
        state.get("selection"),
    )
    _node_log(
        "plan_ops",
        state,
        started,
        status=plan.status,
        ops=len(plan.operations),
        kinds=sorted({op.kind for op in plan.operations}),
        spec_chars=len(spec.text),
        sections_omitted=len(spec.omitted),
    )
    return {
        "plan": plan,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "spec_omitted": list(spec.omitted),
    }


def _bump_attempts(state: dict) -> dict:
    """A draft that failed is still a draft that was attempted.

    Without this, an LlmUnavailable out of `_draft` leaves `attempts` where it was,
    `judge` re-reads the stale plan, `_after_judge` sees the old failing verdict and an
    un-advanced counter, and routes back to `draft` forever.
    """
    return {"attempts": state.get("attempts", 0) + 1}


def _judge_deadline_pass(state: dict) -> dict:
    """Past the deadline the judge does not run, and we say so.

    A synthetic pass stops `_after_judge` retrying, which is what we want — but a pass
    with no failures produces no warnings, and the user would receive unreviewed generated
    claim text with nothing to tell them so. `judge_skipped` is that signal. It is a
    separate channel because `warnings` is verify-only and has no reducer, so a second
    writer would be overwritten rather than merged.
    """
    return {
        "verdict": JudgeVerdict(verdict="pass", failures=[], suggestion=""),
        "judge_skipped": True,
    }


@node_guard("draft", on_error=_bump_attempts)
def _draft(llm: LlmBundle, state: dict) -> dict:
    started = time.monotonic()
    plan = llm.draft(
        state["instruction"],
        state["retrieved"],
        state["history"],
        state.get("critique"),
        state.get("selection"),
    )
    attempt = state.get("attempts", 0) + 1
    _node_log(
        "draft",
        state,
        started,
        attempt=attempt,
        status=plan.status,
        ops=len(plan.operations),
        kinds=sorted({op.kind for op in plan.operations}),
        # The length of what was written, never a character of it.
        out_chars=sum(len(op.model_dump_json()) for op in plan.operations),
    )
    return {
        "plan": plan,
        "attempts": attempt,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


@node_guard("judge", on_deadline=_judge_deadline_pass)
def _judge(llm: LlmBundle, state: dict) -> dict:
    started = time.monotonic()
    plan = state["plan"]
    attempt = state.get("attempts", 0)
    # Nothing to review: a refusal or an empty plan skips the judge's cost entirely.
    if plan.status != "ok" or not plan.operations:
        _node_log("judge", state, started, attempt=attempt, verdict="skipped", failures=0)
        return {"verdict": JudgeVerdict(verdict="pass", failures=[], suggestion="")}
    verdict = llm.judge(state["instruction"], state["retrieved"], plan)
    _node_log(
        "judge",
        state,
        started,
        attempt=attempt,
        verdict=verdict.verdict,
        failures=len(verdict.failures),
    )
    return {
        "verdict": verdict,
        "critique": render_critique(verdict),
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


@node_guard("answer")
def _answer(llm: LlmBundle, state: dict) -> dict:
    started = time.monotonic()
    ans = llm.answer(state["instruction"], state["retrieved"], state["history"])
    # What the MODEL claimed. `_verify` counts what actually checked out.
    _node_log("answer", state, started, citations=len(ans.citations), chars=len(ans.text))
    return {"answer": ans, "llm_calls": state.get("llm_calls", 0) + 1}


# ------------------------------------------------------------------- verify (terminal)


def _verify(state: dict) -> dict:
    """No LLM. Delegates to `ai/verify.py`, imported as `vf` so that a node named
    `_verify` calling a module named `verify` is not a five-minute confusion."""
    if state.get("error"):
        return {"status": "error", "message": state["error"], "operations": []}

    # An unresolved understanding never entered a branch, so there is no plan and no
    # answer to emit — literally the empty list.
    u = state["understanding"]
    if not u.resolved:
        # The budget is spent: a TERMINAL outcome, not another question. Status
        # "no_change" is what stops the client storing it as a pending question and
        # re-sending it forever.
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
        # The unpack that keeps `verify.py` free of `schemas.py`.
        cites = [(c.kind, c.ref, c.quote) for c in ans.citations]
        # An answer built from part of the document always says which part it could not
        # read. `.get` because `retrieve` is skipped on some paths.
        retrieved = state.get("retrieved")
        limits = (
            vf.partial_context_warning(
                retrieved.omitted_sections,
                matched=retrieved.context_matched,
                headed=retrieved.context_headed,
            )
            if retrieved is not None
            else []
        )
        return {
            "status": "answer",
            "message": ans.text,
            "warnings": vf.check_citations(state["doc"], cites) + limits,
            "citations": vf.verified_claim_refs(state["doc"], cites),
            "operations": [],
        }

    # `.get`, not a bare subscript: an intent that reached no generating branch leaves no
    # plan in state, and a KeyError inside a node is a 502 with nothing a user can read.
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

    # An edit planned from PART of the specification says so, exactly as an answer built
    # from part of one does. Read from the state channel rather than from `retrieved`,
    # because `plan_ops` never visits `retrieve` and so leaves no `Retrieved` behind —
    # the branch where a blind `replace_text` is likeliest is the branch that had no
    # channel to warn on.
    warnings: list[str] = vf.partial_spec_warning(state.get("spec_omitted") or [])
    # A shipped-but-criticised draft always tells the user why.
    if (verdict := state.get("verdict")) is not None and judge_failed(verdict):
        warnings += [f"Reviewer note: {f}" for f in verdict.failures]
    # A shipped-but-UNREVIEWED draft says so too. The judge's deadline branch returns a
    # synthetic pass to stop the retry loop; without this line it is indistinguishable
    # from a real one and the user receives claim text that nothing checked.
    if state.get("judge_skipped"):
        warnings.append(JUDGE_SKIPPED_NOTE)

    try:
        for op in plan.operations:
            require(op)
    except PlanError as exc:
        return {"status": "error", "message": str(exc), "operations": []}

    # The graph emits OPERATIONS; the route is the sole applier. Applying here as well
    # ran the whole deterministic pipeline twice per request. `require()` stays: it is a
    # pure schema check that turns a malformed plan into a readable terminal error.
    return {
        "status": "edit",
        "message": plan.message,
        "warnings": list(dict.fromkeys(warnings)),
        "operations": plan.operations,
        "citations": [],
    }
