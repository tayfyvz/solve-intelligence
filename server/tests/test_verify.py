"""The deterministic artefact gate, and the citation checks the Q&A branch rests on."""

import pytest

from app.ai.document import parse
from app.ai.outline import OMITTED_MARK
from app.ai.verify import (
    E_COUNT_MISMATCH,
    E_EMPTIED,
    E_NO_HEADING,
    E_NUMBERING,
    E_UNSTABLE,
    SCAFFOLD_RE,
    W_DANGLING_REF,
    W_HEADING_NO_CLAIMS,
    W_NO_CLAIMS,
    W_NO_MATCH,
    W_PARTIAL_NO_HEADINGS,
    W_QUOTED_SCAFFOLD,
    W_SELF_REF,
    VerifyReport,
    check_citations,
    partial_context_warning,
    verified_claim_refs,
    verify,
)
from app.data import SEED_DOCUMENTS
from app.sanitize import sanitize_html

SEED_1 = SEED_DOCUMENTS[0].content
SEED_2 = SEED_DOCUMENTS[1].content

FIVE_CLAIMS = (
    "<h1>Claims</h1>"
    "<p>1. A device comprising a housing.</p>"
    "<p>2. The device of claim 1, wherein the housing is glass.</p>"
    "<p>3. The device of claim 1, wherein the housing is tapered.</p>"
    "<p>4. The device of claim 2, wherein the glass is doped.</p>"
    "<p>5. The device of claim 3, wherein the taper is conical.</p>"
)


@pytest.mark.parametrize(("seed", "count"), [(SEED_1, 8), (SEED_2, 9)])
def test_an_unedited_seed_verifies_clean_and_survives_the_sanitiser(seed: str, count: int) -> None:
    """Two facts in one row, and the second is the one that would otherwise fail 100% of
    edits: the route re-verifies the bytes nh3 produced, not the ones our own serialiser
    did, so any disagreement about entity policy or void-element form fires the
    round-trip error on every single edit. If this fails, the fix is in the formatter.

    It is also the standing proof that Patent 1's real claim-7 defect produces no warning:
    it points at claim 5, which exists. That error is semantic, and a deterministic
    checker cannot see it.
    """
    clean = VerifyReport(ok=True, errors=[], warnings=[])
    assert verify(seed, seed, expected_claims=count) == clean
    assert sanitize_html(seed) == seed  # nh3 is a fixed point on canonical engine output
    assert verify(seed, sanitize_html(seed), expected_claims=count).ok

    edge_shapes = "<p>a</p><hr><ul><li>x</li></ul><blockquote><p>q</p></blockquote><h4>h</h4>"
    assert sanitize_html(edge_shapes) == edge_shapes
    assert verify(edge_shapes, sanitize_html(edge_shapes), expected_claims=None).ok


def test_every_error_blocks_the_edit() -> None:
    """An error means the engine broke a promise it makes, so no user instruction should
    be able to produce one."""
    gapped = "<h1>Claims</h1><p>1. a</p><p>2. b</p><p>4. c</p>"
    assert verify(gapped, gapped, expected_claims=None).errors == [
        E_NUMBERING.format(found="1, 2, 4", n=3)
    ]

    with_heading, without = "<h1>Claims</h1><p>1. a</p><p>2. b</p>", "<p>1. a</p><p>2. b</p>"
    assert E_NO_HEADING in verify(with_heading, without, expected_claims=2).errors
    assert verify(without, with_heading, expected_claims=2).ok  # gaining one is fine

    # The applier's own count, checked against a re-parse. This is what catches a
    # renderer that emits something the parser reads differently.
    assert verify(SEED_1, SEED_1, expected_claims=7).errors == [
        E_COUNT_MISMATCH.format(expected=7, found=8)
    ]
    assert verify(SEED_1, SEED_1, expected_claims=None).ok

    # The strongest check: idempotence evaluated on this document at runtime. In one line
    # it catches unescaped text, a block tag outside BLOCK_TAGS and an inserted claim
    # whose text begins "10. ".
    assert (
        E_UNSTABLE
        in verify("<p>a</p>", "<p>1. <strong>x</strong></p><p>2. y</p>", expected_claims=2).errors
    )

    assert E_EMPTIED in verify(SEED_1, "", expected_claims=0).errors
    assert verify("", "", expected_claims=0).ok

    # …and awkward but legitimate shapes must not error, or an ordinary document would
    # fail every future request with an accusation nobody earned.
    for before, after, expected in [
        ("<p>prose only</p><p>and more</p>", "<p>prose only</p><p>and more</p>", 0),
        ("<h1>Claims</h1><p>1. a</p>", "<h1>Claims</h1><p>1. a</p>", 1),
        ("<p>1. a</p>", "<p>1. a</p>", 0),  # the >=2 fallback reads this as no claims
        ("<h1>Claims</h1><p>1. a</p><p>5. b</p>", "<h1>Claims</h1><p>1. a</p><p>2. b</p>", 2),
        ("<hr>", "<hr>", 0),
    ]:
        assert verify(before, after, expected_claims=expected).ok, before


def test_reference_warnings_report_only_what_this_edit_caused() -> None:
    """Every check that could be true of a document NOBODY edited is computed against
    `before` too. Without the delta, bolding claim 1 reports something true, irrelevant
    and repeated on every request until the user learns to ignore the warnings array —
    which is the day the array stops working.

    Warning order is part of the contract: specific before general.
    """
    before = (
        "<h1>Claims</h1><p>1. A device.</p><p>2. The device of claim 1.</p>"
        "<p>3. The device of claim 4.</p><p>4. The device of claim 1, being blue.</p>"
    )
    after = before.replace("<p>4. The device of claim 1, being blue.</p>", "")
    new_dangling = verify(before, after, expected_claims=3)
    assert new_dangling.ok
    assert new_dangling.warnings == [W_DANGLING_REF.format(number=3, target=4)]

    pre_existing = "<h1>Claims</h1><p>1. A device.</p><p>2. The device of claim 99.</p>"
    bolded = pre_existing.replace("<p>1. A device.</p>", "<p><strong>1. A device.</strong></p>")
    assert verify(pre_existing, bolded, expected_claims=2).warnings == []

    self_ref = FIVE_CLAIMS.replace("4. The device of claim 2,", "4. The device of claim 4,")
    report = verify(FIVE_CLAIMS, self_ref, expected_claims=5)
    assert report.warnings == [W_SELF_REF.format(number=4)]

    emptied = verify(FIVE_CLAIMS, "<h1>Claims</h1>", expected_claims=0)
    assert emptied.warnings == [W_HEADING_NO_CLAIMS, W_NO_CLAIMS]


def test_a_self_reference_the_renumber_manufactured_is_not_blamed_on_the_user() -> None:
    """Claims 1 and 2 are deleted; old claim 4's "of claim 2" is left verbatim because we
    never guess, and the renumber then makes that claim itself claim 2. Reporting it
    would accuse the user of a condition they did not create, on the most ordinary
    deletion in the suite — next to the correct warning that contradicts it."""
    after = (
        "<h1>Claims</h1>"
        "<p>1. The device of claim 1, wherein the housing is tapered.</p>"
        "<p>2. The device of claim 2, wherein the glass is doped.</p>"
        "<p>3. The device of claim 1, wherein the taper is conical.</p>"
    )
    suppressed = verify(FIVE_CLAIMS, after, expected_claims=3, deleted_numbers=frozenset({1, 2}))
    assert suppressed.ok
    assert not [w for w in suppressed.warnings if "refers to itself" in w]

    # The control: the suppression is the argument's doing, not an accident.
    control = verify(FIVE_CLAIMS, after, expected_claims=3)
    assert W_SELF_REF.format(number=1) in control.warnings


def test_citations_are_verified_against_the_document_not_taken_on_trust() -> None:
    """A hallucinated quotation is the failure mode that most damages trust in a legal
    tool, and it costs one substring search to catch. What survives becomes the response's
    `citations`, so a claim number only appears if its quote checks out AND the claim
    exists.

    `prior_art` citations are not checked: the uploaded text is not in the document.
    """
    doc = parse(SEED_1)
    real = "the biocompatible materials are glass"
    citations = [
        ("claim", "2", real),
        ("claim", "3", "invented out of thin air"),
        ("claim", "99", real),  # no such claim
        ("prior_art", "uploaded file", "not in the document, and not checked"),
    ]

    warnings = check_citations(doc, citations)
    assert len(warnings) == 1
    assert "invented out of thin air" in warnings[0] and "claim 3" in warnings[0]
    assert verified_claim_refs(doc, citations) == [2]


def test_a_quoted_placeholder_is_told_apart_from_an_invented_quote() -> None:
    """A quote that is not in the document but IS one of this program's own elision
    markers is the model faithfully reading scaffolding we put in front of it. Calling
    that a hallucination leaks an internal string and gives the user nothing to do.

    `verify.py` deliberately does not import `outline.py`, so the two agree by this test
    rather than by an import.
    """
    doc = parse(SEED_1)
    marker = OMITTED_MARK.format(n=17, s="s")
    assert SCAFFOLD_RE.search(marker)

    warnings = check_citations(doc, [("section", "Background", marker)])
    assert warnings == [W_QUOTED_SCAFFOLD]
    assert "17" not in warnings[0]  # no internal counts leak

    # Deduplicated: the sentence carries no quote, so three markers would print it thrice.
    assert check_citations(doc, [("section", "A", marker), ("section", "B", marker)]) == warnings


def test_the_partial_context_warning_says_which_of_three_things_happened() -> None:
    """The same omission means three different things, and only one of them is "ask about
    that section". Naming a section is unfollowable with no headings — and a user who
    tries makes retrieval WORSE, because the words in "the opening text (no heading)"
    match nothing. Saying only "I did not see all of X" when nothing matched is literally
    true and implies the opposite of the truth.

    On the common path — a document read whole — it must stay silent, or it is a warning
    on every answer and nobody reads it.
    """
    assert partial_context_warning([]) == []
    assert partial_context_warning(["", ""]) == []

    labels = [f"SECTION {i}" for i in range(9)]
    (named,) = partial_context_warning(labels)
    assert '"SECTION 0"' in named and "and 4 more" in named and "SECTION 8" not in named

    no_match, still_named = partial_context_warning(labels, matched=False)
    assert no_match == W_NO_MATCH  # first, because it changes what the second one means
    assert still_named == named

    (unheaded,) = partial_context_warning(labels, headed=False)
    assert unheaded == W_PARTIAL_NO_HEADINGS
    assert "Quote a phrase" in unheaded and "by name" not in unheaded
