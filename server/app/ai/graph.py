"""What order the steps run in, and how the outside world calls them.

The only file in this repository that imports `langgraph`. It holds the state channels,
the three conditional edges, the retry bound and the two entry points; *what* each step
does is in `nodes.py`.

LangGraph earns its place here for one reason: the pipeline contains a genuine cycle,
`draft ⇄ judge`. Everything else is a straight line.

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
    UnderstandFn,
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

    A key that is not declared here DOES NOT EXIST at runtime. `StateGraph(State)` builds
    its channels from these annotations, and anything `invoke()` is handed that is not a
    declared channel is silently dropped — no warning, no error; the node just reads
    `None`. That is how `prior_art_name` was seeded, read and never once present. G13
    asserts that every key `run_plan` seeds is declared here.

    `total=False` because LangGraph merges the partial dicts nodes return, so every key
    except the inputs is optional.
    """

    # ---- inputs, written once by run_plan, read-only thereafter -------------
    instruction: str
    html: str
    doc: ParsedDocument  # parsed once in run_plan, never re-parsed in a node
    outline: str
    prior_art: str  # raw user text, NOT yet stripped or fenced
    prior_art_name: str | None  # FILENAME ONLY. `understand` sees this and never the
    # contents (§21.3, §22.12) — which is why it must be a
    # declared channel and not an implicit passenger.
    selection: Selection | None
    history: list[Turn]
    started_at: float  # time.monotonic() — the deadline check reads this
    req: str  # per-request UUID4 hex, minted in the route. The ONLY thing that
    # correlates a /chat log line to the /apply that followed it, and
    # it costs one field. Not a tracing system: no
    # spans, no propagation to the OpenAI call, no sampling.

    pending_question: str | None  # the clarifying question we asked last turn, or None
    clarify_count: int  # consecutive clarifications, floored+clamped by the route

    # ---- written by `understand` --------------------------------------------
    understanding: Understanding
    intent: Intent  # == understanding.intent; a convenience for the edges
    routed_by: Literal["deterministic", "keyword", "llm"]

    # ---- written by `retrieve` ---------------------------------------------
    retrieved: Retrieved

    # ---- written by `plan_ops` and by `draft` ------------------------------
    plan: EditPlan

    # ---- written by `judge`; `attempts` by `draft` AND by draft's guard -----
    verdict: JudgeVerdict
    attempts: int  # 0 on entry; incremented on EVERY draft attempt,
    # including one that failed
    critique: str | None  # rendered verdict, consumed by draft on retry
    judge_skipped: bool  # set ONLY by judge's deadline branch. `verify` turns it
    # into the reviewer note that stops us shipping
    # unreviewed generated prose in silence (§3.4 point 2)

    # ---- written by `answer` -----------------------------------------------
    answer: Answer

    # ---- written by `verify` (terminal) ------------------------------------
    warnings: list[str]
    citations: list[int]
    options: list[str]  # clickable prefilled instructions; clarification only
    operations: list[Op]
    status: Literal["edit", "answer", "clarification", "no_change", "error"]
    message: str

    llm_calls: int  # incremented where a wrapper is ACTUALLY called, never in the
    # guard: judge returns early on an empty plan and the deadline
    # branch returns without calling, so guard-counting would overstate
    # the bill on exactly the paths worth trusting the number on.

    # ---- written by any node's guard when it catches LlmUnavailable ---------
    error: str | None


# ------------------------------------------------------------------- the retry bound


def max_draft_attempts() -> int:
    """ONE source of truth for the retry bound, read AT CALL TIME.

    `judge_max_retries` is the config setting and means "extra draft attempts"; this is
    derived from it so the two can never drift. Do not introduce a third name.

    It is a FUNCTION, not a module constant. `MAX_DRAFT_ATTEMPTS = get_settings().x + 1`
    at import time did three bad things at once: it populated the settings lru_cache
    during module import (so any test that later varied the settings got the import-time
    value back), it made the bound unparametrisable — G15 could not exist — and it made
    §30.1's "set judge_max_retries = 0 and the worst case drops to 38 s" fallback lever a
    lie, because the running process would still use the value it read at import.
    """
    return get_settings().judge_max_retries + 1  # 1 + 1 = 2


# --------------------------------------------------------------- the conditional edges
#
# THE RULE, stated once and applied three times: the FIRST line of every conditional edge
# is `if state.get("error"): return "verify"`.
#
# A conditional edge runs *after* a node, on whatever the node returned. When a node's
# guard caught an LlmUnavailable it returned {"error", "status"} and NOTHING ELSE — no
# `understanding`, no `verdict`, no `intent`. Every edge below reads exactly one of those
# keys, and `State` is total=False, so the key is genuinely absent and a bare lookup
# raises KeyError INSIDE the routing function, which LangGraph propagates out of
# `invoke()`: the run never reaches `verify`, and the route sees an exception instead of
# status == "error". One line per edge, three edges, no exceptions.


def _branch(state: State) -> str:
    """An unresolved understanding goes STRAIGHT to `verify`, which has no plan and
    therefore emits no operations. Not "is rejected by plan_ops" — never reaches it."""
    if state.get("error"):  # THE RULE
        return "verify"
    u = state["understanding"]
    if not u.resolved:
        return "verify"
    # .get with a fallback, never a bare lookup: `Intent` is a Literal today, but a
    # KeyError raised INSIDE an edge is an uncaught 502 with no message a user could read,
    # whereas an unmapped intent routed to `verify` is a clean "nothing changed". The
    # warning is how we find out it happened.
    target = {"edit_ops": "plan_ops", "generate": "retrieve", "answer": "retrieve"}.get(
        u.intent, "verify"
    )
    if target == "verify":
        logger.warning("ai.edge=_branch unmapped intent=%r — routing to verify", u.intent)
    return target


def _after_retrieve(state: State) -> str:
    """Named, not a lambda, for the same three reasons every edge is: the error guard, the
    exhaustive map, and a traceback that says which edge failed.

    `edit_ops` never reaches `retrieve` — `_branch` sends it to `plan_ops` — but the map
    covers it anyway. An edge whose map is not exhaustive over its input type is a
    KeyError waiting for the day someone adds a fourth Intent.
    """
    if state.get("error"):  # THE RULE
        return "verify"
    intent = state.get("intent")
    target = {"generate": "draft", "answer": "answer", "edit_ops": "verify"}.get(intent, "verify")
    if target == "verify":
        logger.warning("ai.edge=_after_retrieve unexpected intent=%r — routing to verify", intent)
    return target


def _after_judge(state: State) -> str:
    """Retry the draft at most `max_draft_attempts()` times, then ship the best effort with
    the judge's complaints attached as warnings. An unbounded critic loop is the classic
    way an agent burns a budget on a document nobody will ever be fully happy with.

    The error guard here is not merely defensive. `draft` fails with LlmUnavailable — the
    single most likely LLM failure in the system, because `_parse` raises it on
    finish_reason == "length" and `draft` is the longest generation we ask for. The
    `draft → judge` edge is unconditional, so `judge` runs next; its guard short-circuits
    on `error` and returns {}, leaving the PREVIOUS attempt's verdict in state. Without the
    guard below, this edge would read that stale failing verdict and route back to `draft`.
    """
    if state.get("error"):  # THE RULE
        return "verify"
    if judge_failed(state["verdict"]) and state.get("attempts", 0) < max_draft_attempts():
        return "draft"
    return "verify"


# ------------------------------------------------------------------------- the graph


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

    # NO CHECKPOINTER — deliberately.
    #
    # A checkpointer exists to survive a pause. The only pause in this design is the user
    # confirming a generative edit, and that pause is handled by returning the operations
    # and letting the client hand them back (two runs), not by interrupt(). Three reasons:
    #   1. interrupt() REQUIRES a checkpointer, and an in-memory one loses every pending
    #      run on each reload — the dev server runs `uvicorn --reload`, so that is every
    #      file save during a live pairing round.
    #   2. interrupt() resumes by REPLAYING the node from its start, which re-executes the
    #      LLM call that produced the draft. The user would confirm one text and receive
    #      another.
    #   3. A SqliteSaver would put opaque pickled graph state in the same database as the
    #      documents, which invariant 2 forbids. It also is not bundled: 3 more packages.
    # The whole run is a few seconds and fully re-runnable; there is nothing worth
    # resuming. G12 asserts it.
    #
    # Built per call rather than once at import: `LlmBundle` is injected per request,
    # compile() on a seven-node graph is sub-millisecond, and it is what makes
    # max_draft_attempts() genuinely re-read per run.
    return g.compile()


# ------------------------------------------------------------- the route-facing seam


@dataclass(frozen=True)
class GraphInput:
    html: str  # exactly the bytes the client sent
    instruction: str
    prior_art: str = ""  # "" when no file is attached
    prior_art_name: str | None = None  # filename only; understand never sees the contents
    selection: Selection | None = None
    history: list[Turn] = field(default_factory=list)  # already truncated by the route
    pending_question: str | None = None  # the clarifying question we asked last turn
    clarify_count: int = 0  # already clamped by the route
    req: str = ""  # per-request correlation id; "" in tests that do not care


@dataclass(frozen=True)
class GraphResult:
    status: Literal["edit", "answer", "clarification", "no_change", "error"]
    message: str  # one short sentence for the user, never JSON
    operations: list[Op]  # [] unless status == "edit"
    warnings: list[str]
    citations: list[int]  # claim numbers, [] unless status == "answer"
    options: list[str]  # [] unless status == "clarification"
    llm_calls: int = 0  # what this run actually spent, for the terminal log line


AiRunner = Callable[[GraphInput], GraphResult]


def _initial_state(gi: GraphInput, doc: ParsedDocument) -> State:
    """The one place the initial state is built, so G13 can assert that every key it seeds
    is a declared channel rather than trusting a second copy of the list."""
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


def run_plan_initial_state_keys() -> tuple[str, ...]:
    """Derived, never restated: the keys `run_plan` actually seeds."""
    return tuple(_initial_state(GraphInput(html="", instruction=""), parse("")))


def run_plan(gi: GraphInput, llm: LlmBundle) -> GraphResult:
    """Run 1. Zero document change, always. Takes no Session — invariant 2 in the
    signature."""
    doc = parse(gi.html)
    graph = build_graph(llm)
    # recursion_limit is the STRUCTURAL bound on the draft ⇄ judge cycle, and it is
    # deliberately tight — but it is DERIVED, from the same function the retry loop reads,
    # because the two bound the same loop and must not be able to disagree:
    #   understand(1) + retrieve(1) + (draft→judge)×k(2k) + verify(1) = 2k+3 super-steps
    #   + 1 step of headroom                                          = 2k+4
    # k = max_draft_attempts() ⇒ 8 at the default, 10 at judge_max_retries = 2. A
    # hard-coded 8 silently caps judge_max_retries at 1: at 2 the LEGITIMATE path is nine
    # super-steps, so a correct run would raise GraphRecursionError and tell the user the
    # AI got stuck. LangGraph's default of 25 would let a cycle bug burn ~18 extra LLM
    # calls and blow every layer of §3.4's timeout chain before anything noticed.
    limit = 2 * max_draft_attempts() + 4
    try:
        terminal = graph.invoke(_initial_state(gi, doc), config={"recursion_limit": limit})
    except GraphRecursionError:
        # If this fires it is a bug in an edge, not a user problem — so it is logged at
        # ERROR with the instruction's LENGTH (never the instruction) and reported as a
        # plain sentence, never as a stack trace. The document is provably byte-identical,
        # because the graph never mutates it.
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
    """The FastAPI dependency the R-tests override. Builds the real bundle and runs the
    real graph.

    `app.ai.llm` is imported INSIDE the function body. That is what keeps `openai` out of
    `sys.modules` for every test that does not override the runner, and it keeps T5's
    guarantee mechanical rather than aspirational.
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


__all__ = [
    "AiRunner",
    "GraphInput",
    "GraphResult",
    "LlmBundle",
    "RECURSION_MESSAGE",
    "State",
    "UnderstandFn",
    "build_graph",
    "get_ai_runner",
    "max_draft_attempts",
    "run_plan",
    "run_plan_initial_state_keys",
]
