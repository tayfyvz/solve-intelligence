"""What order the steps run in, and how the outside world calls them.

The only file that imports `langgraph`. It holds the state channels, the three
conditional edges, the retry bound and the entry point; *what* each step does is in
`nodes.py`. LangGraph earns its place for one reason: the pipeline contains a genuine
cycle, `draft ⇄ judge`. Everything else is a straight line.

    understand ──┬─► plan_ops ─────────────────────► verify ─► END
                 ├─► retrieve ─► draft ⇄ judge ────►
                 ├─► retrieve ─► answer ───────────►
                 └─(not resolved)──────────────────►
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from app.ai.document import ParsedDocument, parse
from app.ai.nodes import (
    LlmBundle,
    _answer,
    _draft,
    _judge,
    _plan_ops,
    _retrieve,
    _understand,
    _verify,
)
from app.ai.outline import build_outline
from app.ai.prompts import Selection
from app.ai.schemas import (
    Answer,
    EditPlan,
    Intent,
    JudgeVerdict,
    Op,
    Retrieved,
    Understanding,
    judge_failed,
)
from app.ai.understand import Turn
from app.config import get_settings

logger = logging.getLogger(__name__)

RECURSION_MESSAGE = (
    "The AI got stuck reviewing its own draft, so nothing was changed. Please try again."
)


class State(TypedDict, total=False):
    """The graph's channels.

    A key that is not declared here does not exist at runtime: `StateGraph(State)` builds
    its channels from these annotations, and anything `invoke()` is handed that is not a
    declared channel is dropped silently — no warning, the node just reads `None`.

    `total=False` because LangGraph merges the partial dicts nodes return.
    """

    # ---- inputs, written once by run_plan, read-only thereafter -------------
    instruction: str
    html: str
    doc: ParsedDocument  # parsed once in run_plan, never re-parsed in a node
    outline: str
    prior_art: str  # raw user text, not yet stripped or fenced
    prior_art_name: str | None  # filename only; `understand` never sees the contents
    selection: Selection | None
    history: list[Turn]
    started_at: float  # time.monotonic() — the deadline check reads this
    req: str  # per-request id, the only thing correlating a /chat log line to its /apply

    pending_question: str | None  # the clarifying question we asked last turn
    clarify_count: int  # consecutive clarifications, floored and clamped by the route

    # ---- written by `understand` --------------------------------------------
    understanding: Understanding
    intent: Intent  # == understanding.intent; a convenience for the edges
    routed_by: Literal["deterministic", "keyword", "llm"]

    # ---- written by `retrieve` ---------------------------------------------
    retrieved: Retrieved

    # ---- written by `plan_ops` and by `draft` ------------------------------
    plan: EditPlan

    # ---- written by `retrieve` AND by `plan_ops` ---------------------------
    # The specification sections the editing branch could not fit. Its own channel and
    # not a field read off `retrieved`, because the two editing routes are not
    # symmetrical: `generate` goes through `retrieve`, `edit_ops` is branched straight to
    # `plan_ops` and leaves no `Retrieved` at all. `verify` needs one place to look.
    spec_omitted: list[str]

    # ---- written by `judge`; `attempts` by `draft` and by draft's guard -----
    verdict: JudgeVerdict
    attempts: int  # incremented on every draft attempt, including one that failed
    critique: str | None  # rendered verdict, consumed by draft on retry
    judge_skipped: bool  # set only by judge's deadline branch; `verify` reads it

    # ---- written by `answer` -----------------------------------------------
    answer: Answer

    # ---- written by `verify` (terminal) ------------------------------------
    warnings: list[str]
    citations: list[int]
    options: list[str]  # clickable prefilled instructions; clarification only
    operations: list[Op]
    status: Literal["edit", "answer", "clarification", "no_change", "error"]
    message: str

    # Incremented where a wrapper is actually called, never in the guard: judge returns
    # early on an empty plan, so guard-counting would overstate the bill.
    llm_calls: int

    # ---- written by any node's guard when it catches LlmUnavailable ---------
    error: str | None


def max_draft_attempts() -> int:
    """One source of truth for the retry bound, read AT CALL TIME.

    A module constant computed at import would populate the settings cache during import
    (so a test that varied the settings got the import-time value back) and would make
    `judge_max_retries` a lever that does nothing in a running process.
    """
    return get_settings().judge_max_retries + 1


# The rule, stated once and applied three times: the first line of every conditional edge
# is `if state.get("error"): return "verify"`.
#
# A conditional edge runs after a node, on whatever the node returned. When a node's guard
# caught an LlmUnavailable it returned {"error", "status"} and nothing else. Every edge
# below reads exactly one other key, and `State` is total=False, so a bare lookup would
# raise KeyError inside the routing function — which LangGraph propagates out of
# `invoke()`, so the run never reaches `verify` and the route sees an exception instead of
# status == "error".


def _branch(state: State) -> str:
    """An unresolved understanding goes STRAIGHT to `verify`, which has no plan and
    therefore emits no operations. Not "is rejected by plan_ops" — never reaches it."""
    if state.get("error"):
        return "verify"
    u = state["understanding"]
    if not u.resolved:
        return "verify"
    # `.get` with a fallback: an unmapped intent routed to `verify` is a clean "nothing
    # changed", where a KeyError raised inside an edge is a 502 with no readable message.
    target = {"edit_ops": "plan_ops", "generate": "retrieve", "answer": "retrieve"}.get(
        u.intent, "verify"
    )
    if target == "verify":
        logger.warning("ai.edge=_branch unmapped intent=%r — routing to verify", u.intent)
    return target


def _after_retrieve(state: State) -> str:
    """`edit_ops` never reaches `retrieve` — `_branch` sends it to `plan_ops` — but the
    map covers it anyway. An edge whose map is not exhaustive over its input type is a
    KeyError waiting for the day someone adds a fourth intent."""
    if state.get("error"):
        return "verify"
    intent = state.get("intent")
    target = {"generate": "draft", "answer": "answer", "edit_ops": "verify"}.get(intent, "verify")
    if target == "verify":
        logger.warning("ai.edge=_after_retrieve unexpected intent=%r — routing to verify", intent)
    return target


def _after_judge(state: State) -> str:
    """Retry the draft at most `max_draft_attempts()` times, then ship the best effort
    with the judge's complaints attached as warnings.

    The error guard here is not merely defensive. `draft` is the longest generation we
    ask for, so it is the likeliest node to fail; the `draft → judge` edge is
    unconditional, so `judge` runs next and its guard short-circuits on `error`, leaving
    the PREVIOUS attempt's verdict in state. Without the guard this edge would read that
    stale failing verdict and route back to `draft`.
    """
    if state.get("error"):
        return "verify"
    if judge_failed(state["verdict"]) and state.get("attempts", 0) < max_draft_attempts():
        return "draft"
    return "verify"


def build_graph(llm: LlmBundle):
    g = StateGraph(State)
    g.add_node("understand", partial(_understand, llm))
    g.add_node("retrieve", _retrieve)  # deterministic, takes no llm
    g.add_node("plan_ops", partial(_plan_ops, llm))
    g.add_node("draft", partial(_draft, llm))
    g.add_node("judge", partial(_judge, llm))
    g.add_node("answer", partial(_answer, llm))
    g.add_node("verify", _verify)  # deterministic

    g.set_entry_point("understand")
    g.add_conditional_edges(
        "understand",
        _branch,
        {"plan_ops": "plan_ops", "retrieve": "retrieve", "verify": "verify"},
    )
    g.add_edge("plan_ops", "verify")
    g.add_conditional_edges(
        "retrieve",
        _after_retrieve,
        {"draft": "draft", "answer": "answer", "verify": "verify"},
    )
    g.add_edge("draft", "judge")
    g.add_conditional_edges("judge", _after_judge, {"draft": "draft", "verify": "verify"})
    g.add_edge("answer", "verify")
    g.add_edge("verify", END)

    # No checkpointer, deliberately. A checkpointer exists to survive a pause, and the
    # only pause here is the user confirming an edit — handled by returning the
    # operations and letting the client hand them back, not by interrupt(). interrupt()
    # would also require replaying the node, so the user would confirm one text and
    # receive another, and a SqliteSaver would put pickled graph state in the same
    # database as the documents.
    #
    # Built per call rather than once at import: the bundle is injected per request,
    # compiling seven nodes is sub-millisecond, and it is what makes
    # `max_draft_attempts()` genuinely re-read per run.
    return g.compile()


@dataclass(frozen=True)
class GraphInput:
    html: str  # exactly the bytes the client sent
    instruction: str
    prior_art: str = ""  # "" when no file is attached
    prior_art_name: str | None = None  # filename only
    selection: Selection | None = None
    history: list[Turn] = field(default_factory=list)  # already truncated by the route
    pending_question: str | None = None
    clarify_count: int = 0  # already clamped by the route
    req: str = ""  # per-request correlation id


@dataclass(frozen=True)
class GraphResult:
    status: Literal["edit", "answer", "clarification", "no_change", "error"]
    message: str  # one short sentence for the user, never JSON
    operations: list[Op]  # [] unless status == "edit"
    warnings: list[str]
    citations: list[int]  # claim numbers, [] unless status == "answer"
    options: list[str]  # [] unless status == "clarification"
    llm_calls: int = 0  # what this run spent, for the terminal log line


AiRunner = Callable[[GraphInput], GraphResult]


def _initial_state(gi: GraphInput, doc: ParsedDocument) -> State:
    return {
        "instruction": gi.instruction,
        "html": gi.html,
        "doc": doc,
        "outline": build_outline(doc),
        "prior_art": gi.prior_art,
        "prior_art_name": gi.prior_art_name,
        "selection": gi.selection,
        "history": gi.history,
        "pending_question": gi.pending_question,
        "clarify_count": gi.clarify_count,
        "attempts": 0,
        "llm_calls": 0,
        "started_at": time.monotonic(),
        "req": gi.req,
    }


def run_plan(gi: GraphInput, llm: LlmBundle) -> GraphResult:
    """Run 1. Zero document change, always. Takes no Session."""
    doc = parse(gi.html)
    graph = build_graph(llm)
    # The structural bound on the draft ⇄ judge cycle, derived from the same function the
    # retry loop reads so the two cannot disagree:
    #   understand(1) + retrieve(1) + (draft→judge)×k(2k) + verify(1) + 1 spare = 2k+4
    # A hard-coded 8 would silently cap `judge_max_retries` at 1, turning a correct run
    # at 2 into "the AI got stuck". LangGraph's default of 25 would let a cycle bug burn
    # ~18 extra LLM calls before anything noticed.
    limit = 2 * max_draft_attempts() + 4
    try:
        terminal = graph.invoke(_initial_state(gi, doc), config={"recursion_limit": limit})
    except GraphRecursionError:
        # A bug in an edge, not a user problem: logged at ERROR with the instruction's
        # LENGTH, never the instruction, and reported as a plain sentence. The document is
        # provably byte-identical, because the graph never mutates it.
        logger.error(
            "ai.graph recursion_limit=%d hit — an edge is cycling; instruction_chars=%d",
            limit,
            len(gi.instruction),
        )
        return GraphResult(
            status="error",
            message=RECURSION_MESSAGE,
            operations=[],
            warnings=[],
            citations=[],
            options=[],
        )
    return GraphResult(
        status=terminal["status"],
        message=terminal["message"],
        operations=terminal.get("operations", []),
        warnings=terminal.get("warnings", []),
        citations=terminal.get("citations", []),
        llm_calls=terminal.get("llm_calls", 0),
        options=terminal.get("options", []),
    )


def get_ai_runner() -> AiRunner:
    """The FastAPI dependency the route tests override.

    `app.ai.llm` is imported inside the function body: that is what keeps `openai` out of
    `sys.modules` for every test that does not override the runner.
    """
    from app.ai import llm as llm_module

    bundle = LlmBundle(
        understand=llm_module.understand_llm,
        plan=llm_module.plan_llm,
        draft=llm_module.draft_llm,
        judge=llm_module.judge_llm,
        answer=llm_module.answer_llm,
    )
    return lambda gi: run_plan(gi, bundle)
