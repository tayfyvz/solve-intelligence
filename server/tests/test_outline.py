"""T8, T10 — the three plain-text views, and the truncation ladders that bound them."""

import re

import pytest

from app.ai.document import parse
from app.ai.outline import (
    CONTEXT_TAIL,
    OUTLINE_HEADER,
    build_context,
    build_outline,
    claims_excerpt,
    section_excerpt,
)
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content
SEED_2 = SEED_DOCUMENTS[1].content

# 400-odd characters per claim, so tiers 1-3 all bite before the middle is dropped.
FILLER = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do " * 7
SIXTY_CLAIMS = "<h1>Claims</h1>" + "".join(f"<p>{n}. {FILLER}</p>" for n in range(1, 61))


def claim_lines(outline: str) -> list[str]:
    return [line for line in outline.splitlines() if line.startswith("  ")]


@pytest.mark.parametrize(("seed", "count"), [(SEED_1, 8), (SEED_2, 9)])
def test_build_outline(seed: str, count: int) -> None:
    """T8 — the planner's view of the document."""
    out = build_outline(parse(seed))
    assert out.splitlines()[0] == OUTLINE_HEADER
    assert len(claim_lines(out)) == count
    assert "<" not in out  # plain text: the model must not start thinking in markup
    assert len(out) <= 8000


def test_build_outline_reports_multi_paragraph_claims() -> None:
    """T8 — Patent 1's claim 1 spans five paragraphs and the outline must say so."""
    assert "[+4 more paragraphs]" in build_outline(parse(SEED_1))


def test_build_outline_does_not_truncate_when_it_fits() -> None:
    """T8 — tier 1 with room to spare: all 60 claims, untouched."""
    out = build_outline(parse(SIXTY_CLAIMS), max_chars=100_000)
    assert len(claim_lines(out)) == 60


def test_build_outline_applies_every_truncation_tier() -> None:
    """T8 — the full ladder on a document no seed can reach.

    NOTE: the cap here is 1_600, not the 1_200 originally planned. Tier 4 keeps ten claim lines at
    each end plus the omitted-range line, and at tier 3's 60-character limit each of
    those costs ~67 characters, so the SHORTEST outline this document can produce is
    1_525 characters. `max_chars=1_200` is unreachable by construction. The plan says as
    much one paragraph earlier -- "tier 4 is not a guarantee of length" -- and the two
    statements cannot both stand. What is asserted below is the behaviour that matters:
    every tier fires, and the ladder bottoms out rather than looping.
    """
    doc = parse(SIXTY_CLAIMS)
    out = build_outline(doc, max_chars=1_600)
    lines = claim_lines(out)

    assert len(out) <= 1_600
    assert len(lines) == 21  # 10 + the omitted-range line + 10
    assert [line for line in lines if "omitted" in line] == ["  … (claims 11–50 omitted) …"]
    assert lines[0].startswith("  1. ")
    assert lines[-1].startswith("  60. ")

    # Tier 3 was reached: every claim line carries at most 60 characters of claim text.
    for line in lines:
        if "omitted" not in line:
            text = re.sub(r"^\s+\d+[.)] ", "", line)
            assert len(text.rstrip("…")) <= 60

    # The ladder bottoms out: nothing below 1_525 is reachable, and asking for less
    # returns the same string rather than shrinking further or looping.
    assert build_outline(doc, max_chars=1) == out
    assert build_outline(doc, max_chars=1_600) == out  # deterministic


@pytest.mark.parametrize(
    ("seed", "blocks_per_claim"),
    [(SEED_1, [5, 1, 1, 4, 1, 5, 1, 1]), (SEED_2, [6, 1, 1, 1, 1, 1, 5, 1, 1])],
)
def test_build_context(seed: str, blocks_per_claim: list[int]) -> None:
    """T10 — the Q&A view carries every claim in full, not truncated at 240."""
    doc = parse(seed)
    view = build_context(doc, max_chars=30_000)
    out = view.text
    assert len(out) <= 30_000
    assert "<" not in out
    for claim in doc.claims:
        # The bracket form, so echoed context can never be read back as a claim prefix.
        assert f"[{claim.number}] " in out
        for block in claim.blocks:
            assert block.html.split("<")[0][:60] in out
    assert [len(c.blocks) for c in doc.claims] == blocks_per_claim
    # A seed fits whole, so tier 1 wins: nothing omitted, and therefore no warning.
    assert view.omitted == ()
    assert view.omitted_paragraphs == 0


def test_build_context_hard_cuts_a_pathological_document() -> None:
    """T10 — tier 5, the only tier that is a guarantee.

    NOTE: reaching tier 5 needs a claim with many paragraphs, not one enormous one.
    The "200 KB single-claim document" case is bounded by tier 4 (which caps the
    first block at 600 characters) and never reaches the hard cut, so it cannot assert
    the tail. Both shapes are asserted here -- the bound holds either way.
    """
    many_blocks = (
        "<h1>Claims</h1><p>1. start</p>"
        + "".join(f"<p>{'y' * 200}</p>" for _ in range(1_000))
        + "<p>2. b</p>"
    )
    view = build_context(parse(many_blocks), max_chars=30_000)
    assert len(view.text) <= 30_000
    assert CONTEXT_TAIL in view.text
    # Tier 5 cuts the STRING; it must not cut the honesty. It used to: a blind end-to-end
    # byte cut severed the NOT SHOWN block, leaving ANSWER_SYSTEM rule 2b naming a marker
    # the model had never been given, on exactly the documents where it matters most. The
    # manifest is now re-attached AFTER the cut, so it is the one thing that always
    # survives — which is why the tail is no longer the last thing in the string.
    assert view.omitted != ()
    assert "--- NOT SHOWN IN FULL ---" in view.text
    assert view.text.rstrip().endswith("Never guess at it.")

    one_huge = "<h1>Claims</h1><p>1. " + "x" * 200_000 + "</p><p>2. b</p>"
    assert len(build_context(parse(one_huge), max_chars=30_000).text) <= 30_000


def test_claims_excerpt_selects_and_never_truncates() -> None:
    """The view every generating node reads. Truncating the text a node is about to
    rewrite is the defect the live pre-flight found."""
    doc = parse(SEED_1)
    out = claims_excerpt(doc, [5, 2])
    assert out.splitlines()[0] == "RELEVANT CLAIMS, IN FULL"
    assert out.index("[2] ") < out.index("[5] ")  # filtered against the parse, sorted
    assert doc.claims[1].blocks[0].html in out  # in full


@pytest.mark.parametrize("numbers", [[], [99], [0, 400]])
def test_claims_excerpt_is_empty_for_unknown_claims(numbers: list[int]) -> None:
    """The caller omits the block entirely rather than emitting a bare header."""
    assert claims_excerpt(parse(SEED_1), numbers) == ""


# ------------------------------------------------------------- section_excerpt (draft's
# view of a resolved non-claim section — the fix for "make the Appendix more professional"
# reaching `draft` with nothing but the outline's one-line heading list)

APPENDIX_DOC = (
    "<h2>Appendix</h2><p>Inception. The Matrix. Interstellar.</p><h1>Claims</h1><p>1. A widget.</p>"
)


def test_section_excerpt_returns_the_full_body() -> None:
    out = section_excerpt(parse(APPENDIX_DOC), "Appendix")
    assert out.splitlines()[0] == "RELEVANT SECTION, IN FULL — Appendix"
    assert "Inception. The Matrix. Interstellar." in out


def test_section_excerpt_matches_heading_case_insensitively() -> None:
    assert "Interstellar" in section_excerpt(parse(APPENDIX_DOC), "appendix")


@pytest.mark.parametrize("heading", [None, "", "No Such Section"])
def test_section_excerpt_is_empty_without_a_match(heading: str | None) -> None:
    assert section_excerpt(parse(APPENDIX_DOC), heading) == ""


# ------------------------------------- hand-typed pseudo-headings (product owner's bug)
#
# A user who adds a new "section" by selecting a short line and bolding it, instead of
# using the H1/H2/H3 toolbar buttons, produces a plain <p> with a whole-block bold mark —
# not a heading tag. document.py's claims-region absorption then folds that paragraph
# (and whatever follows it) into the preceding claim's continuation blocks; a paragraph
# before the claims heading with the same shape lands, unlabeled, inside whichever real
# section's body it sits in. Either way `_headings()` never sees it, so `understand` -
# which is told "the OUTLINE does not list it" means "this document does not contain
# it" - dead-ends on a question naming it, even though the words are right there in the
# document. These tests pin `build_outline`'s fix: name the words, don't move them.

DETAILS_AFTER_CLAIMS = (
    "<p>Field of invention text.</p><h1>Claims</h1>"
    "<p>1. A device comprising a widget.</p>"
    "<p><strong>Details</strong></p>"
    "<p>The widget is round and blue in preferred embodiments.</p>"
)

DETAILS_BEFORE_CLAIMS = (
    "<h2>Background</h2><p>Background text here.</p>"
    "<p><strong>Details</strong></p><p>The widget is round and blue.</p>"
    "<h1>Claims</h1><p>1. A device comprising a widget.</p>"
)

TWO_PSEUDO_HEADINGS = (
    "<p>Field.</p><h1>Claims</h1><p>1. A device.</p>"
    "<p><strong>Details</strong></p><p>Round and blue.</p>"
    "<p><strong>Test Results</strong></p><p>Passed all tests.</p>"
)


def test_pseudo_heading_swallowed_into_a_claim_is_named_in_the_outline() -> None:
    """Case 1 (the reported bug): a hand-typed 'section' after the claims, with no
    heading tag, is folded into claim 1's continuation blocks by document.py. The outline
    must still name it, or `understand` can never resolve a question about it."""
    doc = parse(DETAILS_AFTER_CLAIMS)
    assert doc.postamble == []  # confirms the swallow: nothing "after the claims"
    assert len(doc.claims[0].blocks) == 3  # the numbered block + "Details" + its body
    out = build_outline(doc)
    assert '"Details"' in out
    assert "reads as its own heading" in out
    assert "[+2 more paragraphs]" in out  # the existing note is not replaced, only extended


def test_pseudo_heading_in_the_preamble_is_named_in_the_outline() -> None:
    """Case 2: a hand-typed 'section' between two real headings does NOT get swallowed
    into a claim (claims absorption is claims-region-specific) — it merges invisibly into
    the PREVIOUS section's body instead, a milder but related mislabeling."""
    doc = parse(DETAILS_BEFORE_CLAIMS)
    # Confirms the milder defect: "Details" has no heading tag of its own, so it is part
    # of the Background section's body, not a section boundary.
    assert [b.tag for b in doc.preamble] == ["h2", "p", "p", "p"]
    out = build_outline(doc)
    assert "Sections before the claims: Background, Claims (heading)" in out
    assert '"Details"' in out
    assert "reads as its own heading" in out


def test_multiple_pseudo_headings_in_a_row_all_compound_into_one_claim_and_all_are_named() -> None:
    """Case 3: repeated hand-typed sections after the claims compound into the SAME
    claim (document.py's absorption loop has no way to stop early), and the outline must
    name every one of them, not just the first."""
    doc = parse(TWO_PSEUDO_HEADINGS)
    assert len(doc.claims[0].blocks) == 5  # numbered block + 2 x (heading + body)
    out = build_outline(doc)
    assert '"Details"' in out
    assert '"Test Results"' in out


def test_pseudo_heading_content_was_always_reachable_via_build_context() -> None:
    """The other half of the bug: confirms the content itself was NEVER lost. Retrieval
    (`build_context`) already renders a claim's continuation blocks in full, so once
    `understand` resolves the request (now that the outline names it), the answer node
    has always had what it needs. This is why the fix lives in `build_outline`, not in
    `document.py` or `build_context`."""
    doc = parse(DETAILS_AFTER_CLAIMS)
    view = build_context(doc, {"detail"})
    assert "Details" in view.text
    assert "round and blue" in view.text
    assert view.omitted == ()


def test_bold_sentence_is_not_mistaken_for_a_pseudo_heading() -> None:
    """A negative case, guarding the heuristic's bound: bold prose that ends like a
    sentence, or simply runs long, must not be flagged — only a short, unpunctuated,
    wholly-bold line reads as a hand-typed section name."""
    doc = parse(
        "<h1>Claims</h1><p>1. A device.</p>"
        "<p><strong>The widget is round and blue in every embodiment described here.</strong></p>"
    )
    out = build_outline(doc)
    assert "reads as its own heading" not in out


def test_real_multi_paragraph_claim_is_not_flagged_as_a_pseudo_heading() -> None:
    """Regression guard: Patent 1's claim 1 genuinely spans five paragraphs of ordinary
    (unbolded) prose. Nothing about the fix may cause the outline to start treating a
    real claim's continuation as a mislabeled section."""
    out = build_outline(parse(SEED_1))
    assert "reads as its own heading" not in out


def test_pseudo_heading_in_a_headingless_document_is_still_named() -> None:
    """Case 4: a `.txt`-imported document with no headings at all (the `headed=False`
    path in `ContextView`) can still carry a hand-typed pseudo-heading if the user adds
    one after import. The outline's naming does not depend on the document having any
    real headings elsewhere."""
    doc = parse(
        "<p>Plain opening prose with no heading at all.</p>"
        "<p><strong>Details</strong></p><p>More prose, still no real heading anywhere.</p>"
    )
    assert doc.claims == []  # no claims region: everything is preamble
    out = build_outline(doc)
    assert '"Details"' in out
    view = build_context(doc, {"detail"})
    assert view.headed is False
    assert "Details" in view.text
