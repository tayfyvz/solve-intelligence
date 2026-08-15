"""Fake LLM members for the graph tests. No network, no key, no `openai`.

Every member records its call BEFORE it computes or raises anything, so a member that
fails is still counted — which is what the retry-bound tests count.
"""

from __future__ import annotations

import time
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

ANTECEDENT_FAILURE = '"the optical fibre" has no antecedent basis in claim 2.'
CUT_OFF = "The AI's response was cut off. Try a shorter instruction."


def understanding(**overrides) -> Understanding:
    """A resolved, claim-targeted understanding, with any field overridden. A factory
    rather than a constant, so one test's edit cannot reach the next."""
    base = {
        "intent": "edit_ops",
        "restatement": "Make claim 1 bold.",
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
    args: tuple
    kwargs: dict  # `understand` is the only member called with keywords


@dataclass
class Recorder:
    """One entry per call, in call order across all five members, so a test can assert
    ordering as well as counts."""

    calls: list[Call] = field(default_factory=list)

    def count(self, node: str) -> int:
        return sum(1 for c in self.calls if c.node == node)

    def args_for(self, node: str) -> list[Call]:
        return [c for c in self.calls if c.node == node]


def _member(node: str, rec: Recorder, supplied, default):
    """Each member is either a VALUE, returned every time, or a CALLABLE invoked with the
    real arguments. Recording is unconditional and happens first."""

    def call(*args, **kwargs):
        rec.calls.append(Call(node, args, kwargs))
        value = default if supplied is None else supplied
        return value(*args, **kwargs) if callable(value) else value

    return call


def fake_bundle(
    *, understand=None, plan=None, draft=None, judge=None, answer=None, rec: Recorder
) -> LlmBundle:
    """Omitted members return a benign default, so a test asserting "draft was never
    called" needs no setup at all."""
    return LlmBundle(
        understand=_member("understand", rec, understand, DEFAULT_UNDERSTANDING),
        plan=_member("plan", rec, plan, DEFAULT_PLAN),
        draft=_member("draft", rec, draft, DEFAULT_PLAN),
        judge=_member("judge", rec, judge, DEFAULT_VERDICT),
        answer=_member("answer", rec, answer, DEFAULT_ANSWER),
    )


def failing_then_passing_judge(n_failures: int = 1):
    """A judge that fails the first `n_failures` reviews, then passes. This is the whole
    offline test of the cycle: the retry HAPPENS, and it STOPS."""
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


def always_raising(message: str = CUT_OFF):
    """A member that raises LlmUnavailable every time — the most realistic LLM failure we
    have, since `_parse` raises exactly this on `finish_reason == "length"`."""

    def _raise(*args, **kwargs):
        raise LlmUnavailable(message)

    return _raise


def sleeping(seconds: float, value):
    """A member that burns wall clock, for the deadline rows. `time.sleep`, not a mocked
    clock: `node_guard` reads `time.monotonic()`, and a fake clock would test the mock."""

    def _call(*args, **kwargs):
        time.sleep(seconds)
        return value

    return _call


def run_terminal(gi: GraphInput, bundle: LlmBundle) -> State:
    """The full terminal state, for rows that assert on a channel `GraphResult` does not
    carry. Mirrors `run_plan`'s invoke exactly."""
    graph = build_graph(bundle)
    limit = 2 * max_draft_attempts() + 4
    return graph.invoke(_initial_state(gi, parse(gi.html)), config={"recursion_limit": limit})


def assert_document_untouched(terminal: State, html: str) -> None:
    """The graph emits operations, never a document.

    Falsifiable in the way that matters: `state["doc"]` is a mutable object shared by
    every node, so a node that edits it in place — the one way this can actually break —
    changes what `render` produces here.
    """
    assert terminal["html"] == html
    assert render(terminal["doc"]) == render(parse(html))
