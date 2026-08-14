"""G3-G19 — the graph: edges, the draft ⇄ judge cycle, and the three bounds on it.

Every row runs against a fake bundle. No network, no key, no database. The one thing
these tests must be able to fail on is the cycle: an unbounded critic loop costs real
money and a hung browser tab, so the bound is asserted three times, once per mechanism.
"""

from __future__ import annotations

import logging
import time

import pytest

from app.ai import graph as graph_module
from app.ai import prompts
from app.ai.document import block_text, parse
from app.ai.graph import (
    RECURSION_MESSAGE,
    GraphInput,
    GraphResult,
    State,
    _after_judge,
    _after_retrieve,
    _branch,
    build_graph,
    max_draft_attempts,
    run_plan,
    run_plan_initial_state_keys,
)
from app.ai.nodes import DEADLINE_MESSAGE, JUDGE_SKIPPED_NOTE
from app.ai.outline import STOPWORDS, build_context, claims_excerpt, tokens
from app.ai.schemas import Answer, Citation, EditPlan, JudgeVerdict, Op, authors_new_text
from app.data import SEED_DOCUMENTS
from tests.fakes import (
    ANTECEDENT_FAILURE,
    CUT_OFF,
    DEFAULT_PLAN,
    Recorder,
    always_raising,
    assert_document_untouched,
    failing_then_passing_judge,
    fake_bundle,
    run_terminal,
    sleeping,
    succeed_then_raise,
    understanding,
)

SEED_1 = SEED_DOCUMENTS[0].content  # 8 claims; claim 1 spans five paragraphs

INSERT_CLAIM = Op(
    kind="insert_claim",
    after_claim_number=2,
    text="The device of claim 2, wherein the housing comprises borosilicate glass.",
)
GENERATIVE_PLAN = EditPlan(
    status="ok", message="Added a dependent claim after claim 2.", operations=[INSERT_CLAIM]
)


def go(
    instruction: str = "make claim 1 bold",
    *,
    html: str = SEED_1,
    rec: Recorder | None = None,
    members: dict | None = None,
    **gi_kwargs,
) -> tuple[GraphResult, Recorder]:
    rec = rec or Recorder()
    bundle = fake_bundle(rec=rec, **(members or {}))
    return run_plan(GraphInput(html=html, instruction=instruction, **gi_kwargs), bundle), rec


def go_terminal(
    instruction: str = "make claim 1 bold",
    *,
    html: str = SEED_1,
    rec: Recorder | None = None,
    members: dict | None = None,
    **gi_kwargs,
) -> tuple[State, Recorder]:
    rec = rec or Recorder()
    bundle = fake_bundle(rec=rec, **(members or {}))
    gi = GraphInput(html=html, instruction=instruction, **gi_kwargs)
    return run_terminal(gi, bundle), rec


GENERATE = {"understand": understanding(intent="generate", claim_numbers=[2])}
ANSWER = {"understand": understanding(intent="answer", claim_numbers=[4])}


# ----------------------------------------------------------------------- the two paths


def test_deterministic_edit_skips_retrieve_draft_judge() -> None:
    """G3 — "Make claim 1 bold" costs zero LLM calls and never enters the cycle."""
    result, rec = go("make claim 1 bold")
    assert rec.count("draft") == rec.count("judge") == rec.count("answer") == 0
    assert result.status == "edit"
    assert [op.kind for op in result.operations] == ["format_claim"]


def test_a_generative_plan_changes_nothing() -> None:
    """G4 — the graph emits operations; only the route applies them."""
    terminal, _ = go_terminal(
        "add a dependent claim after claim 2",
        members={**GENERATE, "draft": GENERATIVE_PLAN},
    )
    assert terminal["status"] == "edit"
    assert [op.kind for op in terminal["operations"]] == ["insert_claim"]
    assert_document_untouched(terminal, SEED_1)


# ------------------------------------------------------------------ the draft ⇄ judge cycle


def test_judge_retry_loop() -> None:
    """G5 — the retry HAPPENS, and the critique reaches the second draft."""
    rec = Recorder()
    result, _ = go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={**GENERATE, "judge": failing_then_passing_judge(1)},
    )
    assert rec.count("draft") == 2
    assert rec.count("judge") == 2
    assert result.status == "edit"
    critique = rec.args_for("draft")[1].args[3]
    assert isinstance(critique, str) and "antecedent basis" in critique


def test_judge_retry_is_bounded() -> None:
    """G6 — and it STOPS, shipping the best effort with the complaint attached."""
    rec = Recorder()
    result, _ = go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={**GENERATE, "judge": failing_then_passing_judge(99)},
    )
    assert rec.count("draft") == max_draft_attempts() == 2
    assert rec.count("judge") == 2
    assert result.status == "edit"
    assert f"Reviewer note: {ANTECEDENT_FAILURE}" in result.warnings


def test_empty_verdict_fail_is_treated_as_pass() -> None:
    """G7 — a judge that says "fail" but names no defect gives `draft` nothing to act on,
    so the retry would be identical and would burn a call for nothing."""
    rec = Recorder()
    go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={
            **GENERATE,
            "judge": JudgeVerdict(verdict="fail", failures=[], suggestion=""),
        },
    )
    assert rec.count("draft") == 1


# --------------------------------------------------------------------------- the edges


def test_the_graph_never_decides_the_prompt() -> None:
    """G8 — a misroute changes which prompt was used, never whether the user is asked.

    Whether the user confirms is decided by the route from client state, so the words that
    name that decision must not appear in either graph file.
    """
    from pathlib import Path

    result, _ = go(
        "add a dependent claim after claim 2",
        members={"plan": GENERATIVE_PLAN},  # edit_ops intent, generative plan
    )
    assert result.status == "edit"
    assert [op.kind for op in result.operations] == ["insert_claim"]
    assert authors_new_text(GENERATIVE_PLAN) is True

    ai_dir = Path(graph_module.__file__).parent
    for name in ("graph.py", "nodes.py"):
        source = (ai_dir / name).read_text()
        assert "proposal" not in source and "consent" not in source, name


def test_an_unmapped_intent_routes_to_verify(caplog: pytest.LogCaptureFixture) -> None:
    """G9 — a KeyError raised INSIDE a routing function is an uncaught 502 with no message
    a user could read. Routing to `verify` is a clean "nothing changed"."""
    unmapped = understanding().model_copy(update={"intent": "translate"})

    with caplog.at_level(logging.WARNING, logger="app.ai.graph"):
        assert _branch({"understanding": unmapped}) == "verify"
        assert _after_retrieve({"intent": "translate"}) == "verify"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("translate" in r.getMessage() for r in warnings)

    result, rec = go("translate the claims", members={"understand": unmapped})
    assert result.status == "clarification"
    assert result.operations == []
    assert rec.count("plan") == rec.count("draft") == 0


@pytest.mark.parametrize("edge", [_branch, _after_retrieve, _after_judge])
def test_every_conditional_edge_survives_an_errored_state(edge) -> None:
    """G16, the unit half. `{"error": …}` and NOTHING ELSE — no `understanding`, no
    `intent`, no `verdict` — because that is exactly what a guard returns.

    "I remembered the guard in two of three places" is how this comes back.
    """
    assert edge({"error": "boom"}) == "verify"


@pytest.mark.parametrize(
    ("member", "members"),
    [
        ("plan", {}),
        ("draft", GENERATE),
        ("judge", GENERATE),
        ("answer", ANSWER),
    ],
)
def test_a_failure_in_any_llm_node_terminates_readably(member: str, members: dict) -> None:
    """G16, the integration half: every run terminates with a readable message, never an
    exception out of `invoke()`."""
    result, _ = go("do the thing", members={**members, member: always_raising()})
    assert result.status == "error"
    assert result.message == CUT_OFF
    assert result.operations == []


def test_llm_unavailable_never_changes_the_document() -> None:
    """G11 — before the error guard on `_branch`'s first line this test could not have
    reached its own assertion: `_branch` read `state["understanding"]`, which the guard
    never wrote, and the KeyError came out of `invoke()`."""
    terminal, _ = go_terminal("do the thing", members={"understand": always_raising()})
    assert terminal["status"] == "error"
    assert terminal["message"] == CUT_OFF
    assert terminal["operations"] == []
    assert_document_untouched(terminal, SEED_1)


# ------------------------------------------------------------------------- the deadline


def test_the_graph_deadline_is_a_backstop_that_terminates_on_an_llm_node(ai_settings) -> None:
    """G10(a) — the deadline is unreachable in the legitimate configuration by design, so
    the test shrinks the deadline rather than slowing a node past 65 s.

    The SUCCESSOR of the slow node must be an ordinary LLM node for the error path to be
    reachable at all: `draft`'s successor is `judge`, whose deadline branch returns a
    synthetic PASS.
    """
    ai_settings(ai_graph_deadline_seconds=0.05)
    terminal, rec = go_terminal(
        "do the thing",
        members={"understand": sleeping(0.06, understanding())},
    )
    assert terminal["status"] == "error"
    assert terminal["message"] == DEADLINE_MESSAGE
    assert rec.count("plan") == 0
    assert terminal["operations"] == []
    assert_document_untouched(terminal, SEED_1)


def test_the_judge_deadline_ships_the_draft_with_a_reviewer_note(ai_settings) -> None:
    """G10(b) — a synthetic pass stops the retry loop, and `judge_skipped` is what stops it
    being indistinguishable from a real one. Without the note the user receives generated
    claim text that nothing checked, with nothing to tell them so."""
    ai_settings(ai_graph_deadline_seconds=0.05)
    result, rec = go(
        "rewrite claim 2 to be broader",
        members={**GENERATE, "draft": sleeping(0.06, GENERATIVE_PLAN)},
    )
    assert rec.count("draft") == 1
    assert rec.count("judge") == 0
    assert result.status == "edit"
    assert JUDGE_SKIPPED_NOTE in result.warnings


# ---------------------------------------------------------------- state and the bounds


def test_run_plan_seeds_every_declared_channel() -> None:
    """G13 — an undeclared channel is dropped by `invoke()` SILENTLY: no warning, no
    error, the node just reads `None`. That is how `prior_art_name` was seeded, read and
    never once present."""
    _, rec = go(
        "broaden claim 2",
        prior_art="Prior art D1.",
        prior_art_name="prior.txt",
        members={"understand": understanding(claim_numbers=[2])},
    )
    assert rec.args_for("understand")[0].kwargs["prior_art_name"] == "prior.txt"

    # The general form: a typo'd seed key can never be silently dropped either.
    assert set(run_plan_initial_state_keys()) <= set(State.__annotations__)


def test_a_draft_that_always_fails_terminates_in_one_cycle() -> None:
    """G14 — the P3 scenario. `_parse` raises LlmUnavailable on finish_reason == "length"
    and `draft` is the longest generation in the system, so this is the likeliest failure
    we have, not an exotic one."""
    rec = Recorder()
    started = time.monotonic()
    terminal, _ = go_terminal(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={**GENERATE, "draft": always_raising(), "judge": failing_then_passing_judge(99)},
    )
    assert rec.count("draft") == 1
    assert rec.count("judge") == 0  # its guard short-circuits on `error`
    assert terminal["status"] == "error"
    assert terminal["message"] == CUT_OFF
    assert terminal["operations"] == []
    assert time.monotonic() - started < 1.0
    assert_document_untouched(terminal, SEED_1)


def test_the_attempts_counter_alone_also_terminates_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G14, second half — the two fixes are independently sufficient.

    With `_after_judge`'s error guard removed, the run still terminates, because
    `_bump_attempts` advanced the counter on the guard path. The draft succeeds once first
    so that a STALE verdict genuinely exists to be re-read — which is the failure the
    counter fix exists to survive.
    """

    def no_error_guard(state: State) -> str:
        from app.ai.schemas import judge_failed

        if judge_failed(state["verdict"]) and state.get("attempts", 0) < max_draft_attempts():
            return "draft"
        return "verify"

    monkeypatch.setattr(graph_module, "_after_judge", no_error_guard)
    rec = Recorder()
    result, _ = go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={
            **GENERATE,
            "draft": succeed_then_raise(GENERATIVE_PLAN, after=1),
            "judge": failing_then_passing_judge(99),
        },
    )
    assert rec.count("draft") == 2
    assert result.status == "error"
    # The message is what separates the two mechanisms, and it is the whole point of the
    # row. With the counter advancing, `_after_judge` reaches its bound and the run ends
    # at `verify` carrying the draft's own readable failure. WITHOUT it, the edge keeps
    # returning "draft" against a stale verdict, every guard short-circuits, and the run
    # ends only when the recursion limit trips — so `message` becomes RECURSION_MESSAGE.
    # Asserting the status alone passes either way and proves nothing.
    assert result.message == CUT_OFF
    assert result.message != RECURSION_MESSAGE


@pytest.mark.parametrize(
    ("retries", "drafts", "judges"),
    [(0, 1, 1), (2, 3, 3)],
)
def test_the_retry_bound_is_read_from_settings_at_call_time(
    ai_settings, retries: int, drafts: int, judges: int
) -> None:
    """G15 — the call-time reads, both of them.

    At `judge_max_retries = 2` the legitimate path is NINE super-steps. It passes only
    because `recursion_limit` is derived as `2 * max_draft_attempts() + 4` and is
    therefore 10; against a hard-coded 8 this row raises GraphRecursionError and returns
    status="error", so the config lever this test exists to prove would be capped at
    `judge_max_retries <= 1`.
    """
    ai_settings(judge_max_retries=retries)
    rec = Recorder()
    result, _ = go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={**GENERATE, "judge": failing_then_passing_judge(99)},
    )
    assert rec.count("draft") == drafts
    assert rec.count("judge") == judges
    assert result.status == "edit"
    assert f"Reviewer note: {ANTECEDENT_FAILURE}" in result.warnings


# ------------------------------------------------------- the answer branch and context


def test_question_with_no_file_attached() -> None:
    """G17 — the one path that emits no operations and still returns content.

    `claims_text` is asserted by EQUALITY against `build_context`, not by a substring
    check: "it mentions claim 4" would pass on a truncated outline, which is the exact
    defect 4Z found live.
    """
    doc = parse(SEED_1)
    quote = block_text(doc.claims[0].blocks[0])[:40]
    answer = Answer(
        text="Claim 4 depends on claim 1.",
        citations=[
            Citation(kind="claim", ref="1", quote=quote),
            Citation(kind="claim", ref="7", quote="a quote this document does not contain"),
        ],
    )
    question = "what does claim 4 depend on?"
    terminal, rec = go_terminal(question, members={**ANSWER, "answer": answer})

    retrieved = rec.args_for("answer")[0].args[1]
    assert retrieved.claims_text == build_context(doc, tokens(question) - STOPWORDS).text
    assert retrieved.omitted_sections == []  # a seed fits whole; nothing was left out
    assert retrieved.prior_art_excerpt == ""
    assert prompts.prior_art_block_from(retrieved) == ""

    assert terminal["status"] == "answer"
    assert terminal["message"] == answer.text
    assert terminal["operations"] == []
    # Server-computed, never model-supplied: claim 7's quote does not check out, so it is
    # not a citation, and it earns a warning instead.
    assert terminal["citations"] == [1]
    assert len(terminal["warnings"]) == 1
    assert rec.count("plan") == rec.count("draft") == rec.count("judge") == 0
    assert_document_untouched(terminal, SEED_1)


def test_the_generative_nodes_receive_full_claim_text() -> None:
    """G18(a) — 4Z failure A, closed. Live, a model handed a truncated outline and told to
    "rewrite claim 5 to be broader" replied "please provide the full current text of claim
    5" — a correct answer to a badly built prompt."""
    doc = parse(SEED_1)
    rec = Recorder()
    go(
        "rewrite claim 5 to be broader",
        rec=rec,
        members={"understand": understanding(intent="generate", claim_numbers=[5])},
    )
    retrieved = rec.args_for("draft")[0].args[1]
    assert retrieved.claims_text == claims_excerpt(doc, sorted(retrieved.claim_numbers))
    assert block_text(doc.claims[4].blocks[0]) in retrieved.claims_text
    # The two marks `build_outline` leaves behind. Either one means a generating node was
    # prompted from a summary.
    assert "…" not in retrieved.claims_text
    assert "[+" not in retrieved.claims_text
    # The judge reviews what the drafter was shown.
    assert rec.args_for("judge")[0].args[1] is retrieved


def test_plan_ops_receives_full_claim_text_for_the_claims_it_resolved() -> None:
    """G18(b) — `plan_ops` can emit `replace_claim`, and any operation carrying a `text`
    field is authorship, which needs the original."""
    doc = parse(SEED_1)

    rec = Recorder()
    go(
        "replace every 'device' with 'apparatus'",
        rec=rec,
        members={
            "understand": understanding(
                claim_numbers=[], target_kind="whole_document", restatement="Replace it."
            )
        },
    )
    assert rec.args_for("plan")[0].args[2] == claims_excerpt(doc, []) == ""

    rec2 = Recorder()
    go("bold claim 1", rec=rec2)  # the fast path resolves claim 1
    claims = rec2.args_for("plan")[0].args[2]
    assert claims == claims_excerpt(doc, [1])
    assert len(doc.claims[0].blocks) == 5
    for block in doc.claims[0].blocks:
        assert block_text(block) in claims


# ------------------------------------------------------------- the structural bounds


def test_graph_compiles_without_a_checkpointer() -> None:
    """G12 — the design decision gets an assertion, not just a comment."""
    assert build_graph(fake_bundle(rec=Recorder())).checkpointer is None


def test_the_structural_bound_terminates_with_copy_and_no_operations(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """G19(a) — a bound presented as independently sufficient, with its own user-facing
    sentence, that nothing ever executed. The copy could have been wrong and the branch
    could have raised; both would have been found by a reviewer rather than by us."""

    monkeypatch.setattr(graph_module, "_after_judge", lambda state: "draft")
    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="app.ai.graph"):
        result, _ = go(
            "rewrite claim 2 to be broader",
            members={**GENERATE, "judge": failing_then_passing_judge(99)},
        )
    assert result.status == "error"
    assert result.message == RECURSION_MESSAGE
    assert result.operations == []
    assert result.warnings == []
    assert time.monotonic() - started < 1.0

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "recursion_limit=8" in message
    assert "instruction_chars=29" in message
    assert "rewrite claim 2" not in message  # the instruction is never logged verbatim


def test_the_structural_bound_is_derived_not_hard_coded(monkeypatch: pytest.MonkeyPatch) -> None:
    """G19(b) — the configuration form. Forcing the derivation to 2 makes an otherwise
    ordinary run hit the bound, which is what proves `recursion_limit` is the thing being
    read rather than a coincidence of the retry counter."""
    monkeypatch.setattr(graph_module, "max_draft_attempts", lambda: -1)  # ⇒ limit == 2
    result, _ = go("make claim 1 bold")
    assert result.status == "error"
    assert result.message == RECURSION_MESSAGE
    assert result.operations == []


def test_the_derived_limit_admits_a_nine_super_step_run(ai_settings) -> None:
    """G19, the positive control that keeps the bound honest. `judge_max_retries = 2` runs
    understand → retrieve → (draft → judge) × 3 → verify = nine super-steps, and returns
    an edit because the derived limit is 10. A hard-coded 8 turns this correct run into
    `status="error"` blaming the AI for getting stuck."""
    ai_settings(judge_max_retries=2)
    rec = Recorder()
    result, _ = go(
        "rewrite claim 2 to be broader",
        rec=rec,
        members={**GENERATE, "judge": failing_then_passing_judge(99)},
    )
    assert rec.count("draft") == 3
    assert result.status == "edit"
    assert result.operations == DEFAULT_PLAN.operations
