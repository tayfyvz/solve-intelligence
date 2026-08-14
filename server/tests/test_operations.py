"""O1-O11 — the six operations, called directly.

These call the pure functions against `parse(SEED_1)` and assert on `render(doc)`; they
do not go through `apply_plan`, which is 3C. The point of every row is the same one: an
operation given a bad input must leave the document alone and say why, never raise and
never guess.
"""

import pytest

from app.ai.document import escape_text, parse, render
from app.ai.operations import (
    W_ALREADY_DELETED,
    W_EMPTY_FIND,
    W_EMPTY_NEW_CLAIM,
    W_LOST_PARAGRAPHS,
    W_MULTIPLE_HITS,
    W_NO_ANCHOR,
    W_NO_CLAIM,
    W_SPLIT_BY_MARKUP,
    delete_claim,
    format_claim,
    insert_claim,
    insert_section,
    replace_claim,
    replace_text,
)
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content

PAREN_CLAIMS = "<h1>Claims</h1><p>1) A device.</p><p>2) The device of claim 1.</p>"
TWO_CLAIMS_NO_HEADING = "<p>1. A device comprising a housing.</p><p>2. The device of claim 1.</p>"


def seed() -> tuple:
    doc = parse(SEED_1)
    return doc, {c.number: c.uid for c in doc.claims}, []


def test_format_claim_marks_every_block_and_is_reversible() -> None:
    """O1 — every block, because claim 1 is five paragraphs and bolding only the
    numbered one is not what anybody means."""
    doc, uids, warnings = seed()
    format_claim(doc, uids[1], "strong", True, warnings, requested=1)
    out = render(doc)

    assert out.count("<strong>") == 5
    assert warnings == []
    # Claims 2-8 are untouched, byte for byte.
    for claim in parse(SEED_1).claims[1:]:
        for block in claim.blocks:
            assert block.html in out

    format_claim(doc, uids[1], "strong", False, warnings, requested=1)
    assert render(doc) == SEED_1


def test_insert_claim_with_missing_anchor_changes_nothing() -> None:
    """O2 — never append at the end. Never guess a position in a legal document."""
    doc, uids, warnings = seed()
    insert_claim(
        doc, None, "A new claim.", warnings, requested=99, at_start=False, insert_cursor={}
    )
    assert render(doc) == SEED_1
    assert warnings == [W_NO_ANCHOR.format(requested=99)]


def test_insert_claim_with_no_text_is_skipped() -> None:
    """O2 — a claim that says nothing is not a claim."""
    doc, uids, warnings = seed()
    insert_claim(doc, uids[2], "   ", warnings, requested=2, at_start=False, insert_cursor={})
    assert render(doc) == SEED_1
    assert warnings == [W_EMPTY_NEW_CLAIM]


def test_replace_claim_keeps_position_and_warns_about_lost_paragraphs() -> None:
    """O3 — destructive but intended; the warning is what keeps it honest."""
    doc, uids, warnings = seed()
    replace_claim(doc, uids[1], "A much shorter claim.", warnings, requested=1)

    claim = doc.claims[0]
    assert claim.uid == uids[1]
    assert claim.separator == "."
    assert len(claim.blocks) == 1
    assert W_LOST_PARAGRAPHS.format(number=1, count=5) in warnings


def test_replace_text_cannot_corrupt_claim_numbers() -> None:
    """O4 — the payoff of "the number is a field": claim numbers are not in any
    Block.html, so a document-wide replace is structurally incapable of reaching them."""
    doc, _, warnings = seed()
    replace_text(doc, "claim 1", "claim 9", warnings)
    out = render(doc)

    positions = [out.index(f"<p>{n}. ") for n in range(1, 9)]
    assert positions == sorted(positions)  # every prefix present, and still in order
    assert out.count("of claim 9") == 4
    assert "of claim 1" not in out


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda d, w: delete_claim(d, None, w, requested=99, deleted_numbers=set()),
            W_NO_CLAIM.format(requested=99),
        ),
        (
            lambda d, w: format_claim(d, None, "strong", True, w, requested=99),
            W_NO_CLAIM.format(requested=99),
        ),
        (
            lambda d, w: insert_claim(
                d, None, "text", w, requested=99, at_start=False, insert_cursor={}
            ),
            W_NO_ANCHOR.format(requested=99),
        ),
        (
            lambda d, w: replace_claim(d, None, "text", w, requested=99),
            W_NO_CLAIM.format(requested=99),
        ),
        (lambda d, w: replace_text(d, "", "x", w), W_EMPTY_FIND),
    ],
)
def test_unknown_targets_are_no_ops_with_one_warning(call, expected: str) -> None:
    """O5."""
    doc, _, warnings = seed()
    call(doc, warnings)
    assert render(doc) == SEED_1
    assert warnings == [expected]


def test_an_unresolved_delete_does_not_poison_the_remap() -> None:
    """O5 — deleted_numbers drives the cross-reference remap and VF-W2's suppression.
    A delete that resolved to nothing must add nothing to it."""
    doc, _, warnings = seed()
    deleted: set[int] = set()
    delete_claim(doc, None, warnings, requested=99, deleted_numbers=deleted)
    assert deleted == set()

    # And a repeated delete of a claim already gone warns without a second entry.
    uid = doc.claims[0].uid
    delete_claim(doc, uid, warnings, requested=1, deleted_numbers=deleted)
    delete_claim(doc, uid, warnings, requested=1, deleted_numbers=deleted)
    assert deleted == {1}
    assert warnings[-1] == W_ALREADY_DELETED.format(requested=1)


def test_insert_claim_inherits_the_separator() -> None:
    """O6 — a `1) 2) 3)` document never gains a `4.` claim."""
    doc = parse(PAREN_CLAIMS)
    warnings: list[str] = []
    insert_claim(
        doc,
        doc.claims[1].uid,
        "A third device.",
        warnings,
        requested=2,
        at_start=False,
        insert_cursor={},
    )
    assert doc.claims[2].separator == ")"
    assert warnings == []


def test_mark_order_is_deterministic() -> None:
    """O7 — bold-then-italic and italic-then-bold must be the same bytes, or the dirty
    flag lies about a document nobody changed."""
    outputs = []
    for order in (("strong", "em"), ("em", "strong")):
        doc, uids, warnings = seed()
        for mark in order:
            format_claim(doc, uids[2], mark, True, warnings, requested=2)
        outputs.append(render(doc))

    assert outputs[0] == outputs[1]
    assert "<strong><em>2. " in outputs[0]
    assert parse(outputs[0]).claims[1].blocks[0].marks == ("strong", "em")


def test_insert_section_synthesises_a_claims_heading() -> None:
    """O8 — C9. Without the synthesised heading, a Background reading "1. Field of the
    Invention" / "2. Description of Related Art" satisfies the >=2 fallback on the NEXT
    parse and becomes the claims region — the exact bug the model exists to prevent."""
    doc = parse(TWO_CLAIMS_NO_HEADING)
    warnings: list[str] = []
    insert_section(
        doc,
        "Background",
        ["1. Field of the Invention", "2. Description of Related Art"],
        "before_claims",
        warnings,
    )
    out = render(doc)

    assert out.index("<h1>Claims</h1>") < out.index("A device comprising a housing")
    assert warnings == []
    reparsed = parse(out)
    assert len(reparsed.claims) == 2
    assert reparsed.claims[0].blocks[0].html == "A device comprising a housing."


def test_replace_text_split_by_markup_warns_and_leaves_it() -> None:
    """O9 — the honest degradation. Cross-tag surgery is impossible to defend live and
    impossible to test exhaustively."""
    doc = parse("<p>1. A <strong>wireless</strong> device</p>")
    warnings: list[str] = []
    before = render(doc)
    replace_text(doc, "A wireless", "A wired", warnings)
    assert render(doc) == before
    assert warnings == [W_SPLIT_BY_MARKUP.format(find="A wireless")]


def test_replace_text_reports_how_many_it_touched() -> None:
    """O9 — document-wide replacement is the design, so the honest thing is to say how
    many, rather than let the user discover it later."""
    needle = "wireless optogenetic device"
    expected = SEED_1.count(needle)
    assert expected > 1  # the fixture has to actually exercise the branch

    doc, _, warnings = seed()
    replace_text(doc, needle, "apparatus", warnings)
    assert warnings == [W_MULTIPLE_HITS.format(count=expected)]
    assert render(doc).count("apparatus") == expected


@pytest.mark.parametrize(
    "call",
    [
        lambda d, w, u: format_claim(d, u[1], "strong", True, w, requested=1),
        lambda d, w, u: delete_claim(d, u[1], w, requested=1, deleted_numbers=set()),
        lambda d, w, u: insert_claim(
            d,
            u[1],
            "<script>alert(1)</script>",
            w,
            requested=1,
            at_start=False,
            insert_cursor={},
        ),
        lambda d, w, u: replace_claim(d, u[1], "<script>alert(1)</script>", w, requested=1),
        lambda d, w, u: insert_section(d, "<h1>x", [""] * 20, "before_claims", w),
        lambda d, w, u: replace_text(d, "\x00", "<script>", w),
    ],
)
def test_no_operation_raises_on_hostile_input(call) -> None:
    """O10."""
    doc, uids, warnings = seed()
    call(doc, warnings, uids)
    out = render(doc)
    assert "<script" not in out


def test_escape_text_escapes_exactly_once_and_leaves_apostrophes_alone() -> None:
    """O11 — pins C4's quote=False.

    With quote=True the apostrophe would render as `&#x27;`, re-parse as `'`, and
    re-render differently — flipping a byte on every subsequent edit and firing VF-E5 on
    an innocent claim.
    """
    doc, uids, warnings = seed()
    insert_claim(
        doc,
        uids[2],
        'The system\'s <b>housing</b> holds A & B, "tightly".',
        warnings,
        requested=2,
        at_start=False,
        insert_cursor={},
    )
    out = render(doc)

    assert "&#x27;" not in out and "&quot;" not in out  # what TipTap emits, byte for byte
    assert "&amp;" in out and " & " not in out  # escaped exactly once
    assert "<b>" not in out and "&lt;b&gt;" in out  # model markup became literal text
    assert render(parse(out)) == out  # the whole point

    assert escape_text("a'b\"c&d<e") == "a'b\"c&amp;d&lt;e"
