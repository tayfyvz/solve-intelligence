"""U4, U8, U11, U12, U18, U19, U20, U21 — the pure guards on the understanding.

Everything here is a function over a `ParsedDocument` and an `Understanding`. The graph
path through the same functions is 4C's `test_graph_understanding.py`; these are the
guards themselves, and they are what makes an unresolved request structurally incapable
of editing anything.
"""

from dataclasses import dataclass

import pytest

from app.ai.document import parse
from app.ai.schemas import Understanding
from app.ai.understand import (
    _NO_CLAIMS_AT_ALL,
    CAPABILITY_STATEMENT,
    MAX_CLARIFY_TURNS,
    _clean_options,
    claim_refs,
    clarify_floor,
    collapse,
    fast_understanding,
    gate_understanding,
    missing_file_question,
    quote_target,
    resolve_outcome,
)
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content
DOC = parse(SEED_1)  # 8 claims
NO_CLAIMS = parse("<p>Just some prose.</p>")


@dataclass
class Turn:
    role: str
    content: str


def understanding(**overrides) -> Understanding:
    base = {
        "intent": "edit_ops",
        "restatement": "Make claim 3 bold.",
        "reason": "explicit",
        "target_kind": "claims",
        "claim_numbers": [3],
        "section_heading": None,
        "prior_art_role": "none",
        "resolved": True,
        "confidence": "high",
        "question": None,
        "options": [],
    }
    return Understanding(**{**base, **overrides})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("make claim 3 bold", [3]),
        ("make claim3 bold", [3]),
        ("make claim #3 bold", [3]),
        ("make claim three bold", [3]),
        ("bold the 3rd claim", [3]),
        ("bold the third claim", [3]),
        ("bold claims 3 and 5", [3, 5]),
        ("bold claims 3-5", [3, 4, 5]),
        ("bold claims 1, 2 and 4", [1, 2, 4]),
        # The regex must not guess. These need the document or the transcript.
        ("delete the last claim", []),
        ("make it bold", []),
        ("the claimed subject matter", []),
    ],
)
def test_messy_input_normalisation(text: str, expected: list[int]) -> None:
    """U4."""
    assert claim_refs(text) == expected


def test_collapse_normalises_punctuation_and_removes_nuls() -> None:
    """U4 — whitespace, unicode punctuation, NULs. Nothing semantic."""
    assert collapse("  make\n\tclaim  3 bold  ") == "make claim 3 bold"
    assert collapse("the “device”’s — dash") == 'the "device"\'s - dash'
    assert collapse("a\x00b") == "ab"


@pytest.mark.parametrize("count", [0, 1])
def test_clarify_bound_preserves_the_question_while_budget_remains(count: int) -> None:
    """U8."""
    u = understanding(resolved=False, question="Which claim?", options=["Make claim 2 bold"])
    out = resolve_outcome(u, clarify_count=count)
    assert out.question == "Which claim?"
    assert out.clarify_exhausted is False


def test_clarify_bound_produces_a_terminal_outcome() -> None:
    """U8 — the terminal marker. Without it the wire status stays needs_clarification,
    the client stores a pending question, the next turn clamps straight back to 2, and
    the user gets the same capability statement forever. A bound that does not change the
    OUTCOME is not a bound."""
    u = understanding(resolved=False, question="Which claim?", options=["Make claim 2 bold"])
    out = resolve_outcome(u, clarify_count=MAX_CLARIFY_TURNS)
    assert out.question == CAPABILITY_STATEMENT
    assert out.options == []
    assert out.clarify_exhausted is True


def test_the_budget_never_terminates_a_request_we_understood() -> None:
    """U8."""
    out = resolve_outcome(understanding(resolved=True), clarify_count=MAX_CLARIFY_TURNS)
    assert out.clarify_exhausted is False
    assert out.question is None


@pytest.mark.parametrize("count", [99, -5])
def test_resolve_outcome_is_total_over_int(count: int) -> None:
    """U8 — the route floors and clamps; this function must not care."""
    resolve_outcome(understanding(resolved=False, question="?"), clarify_count=count)


def test_low_confidence_never_acts() -> None:
    """U11 — a model saying confidence="low", resolved=True is self-contradictory, and
    the contradiction is exactly the case a prompt-only rule fails on."""
    out = gate_understanding(
        understanding(confidence="low", resolved=True), DOC, "x", clarify_count=0
    )
    assert out.resolved is False
    assert out.question


def test_high_confidence_with_an_honest_question_is_left_alone() -> None:
    """U11 — the reverse direction is NOT "fixed". Turning a question into an action
    would be a guess, and this gate only ever moves towards resolved=False."""
    out = gate_understanding(
        understanding(confidence="high", resolved=False, question="Which claim?"),
        DOC,
        "x",
        clarify_count=0,
    )
    assert out.resolved is False
    assert out.question == "Which claim?"


def test_options_are_complete_instructions() -> None:
    """U12."""
    assert _clean_options(["a", "a", "b", "c", "d", "e"]) == ["a", "b", "c", "d"]
    assert _clean_options(["", "   ", "ok"]) == ["ok"]
    assert _clean_options(["x" * 81]) == []
    assert _clean_options(["x" * 80]) == ["x" * 80]


def test_gate_blocks_a_nonexistent_claim_number() -> None:
    """U18 — this runs BEFORE the branch is chosen, so a nonexistent claim number cannot
    REACH plan_ops or draft. Not "is rejected by them"."""
    out = gate_understanding(understanding(claim_numbers=[12]), DOC, "x", clarify_count=0)
    assert out.resolved is False
    assert out.claim_numbers == []
    assert "12" in out.question and "8" in out.question


def test_gate_handles_a_document_with_no_claims() -> None:
    """U18."""
    out = gate_understanding(
        understanding(claim_numbers=[], target_kind="claims"), NO_CLAIMS, "x", clarify_count=0
    )
    assert out.resolved is False
    assert out.question == _NO_CLAIMS_AT_ALL


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([], 0),
        ([Turn("user", "hi"), Turn("assistant", "Which claim?")], 1),
        (
            [
                Turn("user", "a"),
                Turn("assistant", "Which claim?"),
                Turn("user", "b"),
                Turn("assistant", "Which one though?"),
            ],
            2,
        ),
        (
            [
                Turn("assistant", "Which claim?"),
                Turn("assistant", "Which one?"),
                Turn("assistant", "Really, which?"),
            ],
            MAX_CLARIFY_TURNS,
        ),
        # A statement breaks the run, which is what gives a fresh instruction a fresh budget.
        (
            [
                Turn("assistant", "Which claim?"),
                Turn("assistant", "Which one?"),
                Turn("assistant", "Made claim 3 bold."),
            ],
            0,
        ),
        ([Turn("assistant", "   ")], 0),
    ],
)
def test_clarify_floor_is_derived_from_the_transcript(history: list, expected: int) -> None:
    """U19 — clarify_count arrives from the client and is not evidence of anything. The
    history is the same evidence the model is given, so a client that suppresses it to
    lower this floor also removes the context that makes its own conversation work."""
    assert clarify_floor(history) == expected


def test_clarify_exhausted_is_python_owned() -> None:
    """U20 — Understanding is a Structured Outputs response type, so the model emits this
    field whether or not the prompt names it. Its value is never trusted, in either
    direction."""
    lying = gate_understanding(understanding(clarify_exhausted=True), DOC, "x", clarify_count=0)
    assert lying.clarify_exhausted is False

    honest = gate_understanding(
        understanding(resolved=False, question="Which?", clarify_exhausted=False),
        DOC,
        "x",
        clarify_count=MAX_CLARIFY_TURNS,
    )
    assert honest.clarify_exhausted is True


def test_options_are_empty_on_a_resolved_understanding() -> None:
    """U21 — measured live: the real model returned a resolved, high-confidence
    understanding WITH a populated options list, which the panel would have rendered as
    clarification buttons under a bubble saying the work was done."""
    options = ["Make claim 3 bold", "Make claim 3 italic"]
    resolved = gate_understanding(
        understanding(resolved=True, confidence="high", options=options),
        DOC,
        "x",
        clarify_count=0,
    )
    assert resolved.options == []
    assert resolved.resolved is True  # the guard clears options, it does not ask a question

    unresolved = gate_understanding(
        understanding(resolved=False, question="Which?", options=options),
        DOC,
        "x",
        clarify_count=0,
    )
    assert unresolved.options == options


@pytest.mark.parametrize(
    ("instruction", "numbers"),
    [
        ("make claim 3 bold", [3]),
        ("Make claim 3 italic.", [3]),
        ("bold claim 3", [3]),
        ("unbold claim 3", [3]),
        ("delete claim 3", [3]),
        ("remove claim 8", [8]),
    ],
)
def test_the_fast_path_resolves_three_unambiguous_sentences(
    instruction: str, numbers: list[int]
) -> None:
    """The fast path is an understander, not a router: it returns a fully resolved
    Understanding or None."""
    out = fast_understanding(instruction, DOC, pending_question=None)
    assert out is not None
    assert out.resolved is True
    assert out.intent == "edit_ops"
    assert out.claim_numbers == numbers


@pytest.mark.parametrize(
    "instruction",
    [
        "mak claim3 bold",  # a typo: anchoring makes it fail CLOSED
        "bold the 3rd claim pls",
        "can u make claim three bold",
        "make claim 99 bold",  # a claim number this parse does not contain
        "what is claim 3 about, and make it bold?",  # the compound request that broke pattern 5
        "summarise claim 4 then shorten it",  # the one that broke pattern 6
    ],
)
def test_the_fast_path_falls_through_rather_than_guessing(instruction: str) -> None:
    """U1's regression: the two deleted patterns misrouted compound requests to `answer`
    and the edit half was silently never made."""
    assert fast_understanding(instruction, DOC, pending_question=None) is None


def test_the_fast_path_refuses_while_a_question_is_pending() -> None:
    """ "delete claim 3" after "which claim did you mean?" is data, not an instruction."""
    assert fast_understanding("delete claim 3", DOC, pending_question="Which claim?") is None


def test_a_file_reference_with_no_file_needs_no_llm_call() -> None:
    """Deliberately false-positive-tolerant: the worst case is asking a user who wrote
    "file" for another reason to attach one, and they rephrase."""
    assert missing_file_question("compare this with the attached file", "") is not None
    assert missing_file_question("compare this with the attached file", "prior art") is None
    assert missing_file_question("make claim 3 bold", "") is None


def test_quote_target_is_taken_from_the_parse() -> None:
    """It cannot be hallucinated, because it is a substring of the document."""
    quoted = quote_target(DOC, [2])
    assert quoted
    assert quoted in DOC.claims[1].blocks[0].html
    assert quote_target(DOC, [99]) == ""
    assert quote_target(DOC, []) == ""
