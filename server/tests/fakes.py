"""Fake LLM members for the graph tests. No network, no key, no `openai`.

Every member records its call BEFORE it computes or raises anything, so a member that
fails is still counted — which is exactly what the retry-bound tests count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.document import parse, render
from app.ai.graph import GraphInput, State, _initial_state, build_graph, max_draft_attempts
from app.ai.nodes import LlmBundle
from app.ai.schemas import (
    Answer,
    EditPlan,
    JudgeVerdict,
    LlmUnavailable,
    Op,
    Understanding,
)

BOLD_CLAIM_1 = Op(kind="format_claim", claim_number=1, mark="bold", enabled=True)

DEFAULT_PLAN = EditPlan(status="ok", message="Made claim 1 bold.", operations=[BOLD_CLAIM_1])
DEFAULT_VERDICT = JudgeVerdict(verdict="pass", failures=[], suggestion="")
DEFAULT_ANSWER = Answer(text="Claim 1 is the only independent claim.", citations=[])


def understanding(**overrides) -> Understanding:
    """A resolved, claim-targeted understanding, with any field overridden.

    Written as a factory rather than a constant because every U-test varies one or two
    fields and a shared mutable default would let one test's edit reach the next.
    """
    base = {
        "intent": "edit_ops",
        "restatement": "Make claim 1 bold.",
        "reason": "test fixture",
        "target_kind": "claims",
        "claim_numbers": [1],
        "section_heading": None,
        "prior_art_role": "none",
        "resolved": True,
        "confidence": "high",
        "question": None,
        "options": [],
    }
    return Understanding(**{**base, **overrides})


DEFAULT_UNDERSTANDING = understanding()


@dataclass(frozen=True)
class Call:
    node: str  # "understand" | "plan" | "draft" | "judge" | "answer"
    args: tuple  # positional arguments, verbatim
    kwargs: dict  # keyword arguments, verbatim — `understand` uses these


@dataclass
class Recorder:
    """One entry per call, appended in call order across ALL five members, so a test can
    assert ordering ("judge ran after draft") as well as counts."""

    calls: list[Call] = field(default_factory=list)

    def count(self, node: str) -> int:
        return sum(1 for c in self.calls if c.node == node)

    def args_for(self, node: str) -> list[Call]:
        return [c for c in self.calls if c.node == node]

    def nodes(self) -> list[str]:
        return [c.node for c in self.calls]


def _member(node: str, rec: Recorder, supplied, default):
    """Each member is either a VALUE (returned every time) or a CALLABLE (invoked with the
    real arguments). Recording is unconditional and happens FIRST."""

    def call(*args, **kwargs):
        rec.calls.append(Call(node, args, kwargs))
        value = default if supplied is None else supplied
        return value(*args, **kwargs) if callable(value) else value

    return call


def fake_bundle(
    *, understand=None, plan=None, draft=None, judge=None, answer=None, rec: Recorder
) -> LlmBundle:
    """Omitted members return a benign default, so a test that asserts "draft was never
    called" needs no setup at all. Values are recorded by REFERENCE — the objects passed
    in are frozen Pydantic models or plain strings, which is what lets a test assert on
    the second draft's `critique` text."""
    return LlmBundle(
        understand=_member("understand", rec, understand, DEFAULT_UNDERSTANDING),
        plan=_member("plan", rec, plan, DEFAULT_PLAN),
        draft=_member("draft", rec, draft, DEFAULT_PLAN),
        judge=_member("judge", rec, judge, DEFAULT_VERDICT),
        answer=_member("answer", rec, answer, DEFAULT_ANSWER),
    )


ANTECEDENT_FAILURE = '"the optical fibre" has no antecedent basis in claim 2.'


def failing_then_passing_judge(n_failures: int = 1):
    """A judge that fails the first `n_failures` reviews, then passes.

    This is the entire offline test of the cycle: no key, no network, deterministic, and
    it asserts the two things that can actually break — the retry HAPPENS, and it STOPS.
    """
    state = {"n": 0}

    def _judge(instruction, retrieved, plan) -> JudgeVerdict:
        state["n"] += 1
        if state["n"] <= n_failures:
            return JudgeVerdict(
                verdict="fail",
                failures=[ANTECEDENT_FAILURE],
                suggestion='Introduce "an optical fibre" before referring to it.',
            )
        return DEFAULT_VERDICT

    return _judge


CUT_OFF = "The AI's response was cut off. Try a shorter instruction."


def always_raising(message: str = CUT_OFF):
    """A member that raises LlmUnavailable on every call — the most realistic LLM failure
    we have: `_parse` raises exactly this on finish_reason == "length"."""

    def _raise(*args, **kwargs):
        raise LlmUnavailable(message)

    return _raise


def succeed_then_raise(value=DEFAULT_PLAN, *, after: int = 1, message: str = CUT_OFF):
    """Succeeds `after` times, then raises. Used to reach a state that already holds a
    verdict before the failure, which is the only way to exercise a stale verdict."""
    state = {"n": 0}

    def _call(*args, **kwargs):
        state["n"] += 1
        if state["n"] > after:
            raise LlmUnavailable(message)
        return value

    return _call


def sleeping(seconds: float, value):
    """A member that burns wall clock, for the deadline rows. `time.sleep`, not a mocked
    clock: `node_guard` reads `time.monotonic()` and a fake clock would test the mock."""
    import time

    def _call(*args, **kwargs):
        time.sleep(seconds)
        return value

    return _call


def run_terminal(gi: GraphInput, bundle: LlmBundle) -> State:
    """The full terminal STATE, for the rows that assert on a channel `GraphResult` does
    not carry — `routed_by` is the only one. Mirrors `run_plan`'s invoke exactly."""
    graph = build_graph(bundle)
    limit = 2 * max_draft_attempts() + 4
    return graph.invoke(_initial_state(gi, parse(gi.html)), config={"recursion_limit": limit})


def assert_document_untouched(terminal: State, html: str) -> None:
    """The graph emits operations, never a document.

    Falsifiable in the way that matters: `state["doc"]` is a mutable object shared by
    every node, so a node that edits it in place — the one way this invariant can
    actually break — changes what `render` produces here.
    """
    assert terminal["html"] == html
    assert render(terminal["doc"]) == render(parse(html))
