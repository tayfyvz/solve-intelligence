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

    NOTE: the cap here is 1_600, not PLAN §15's 1_200. Tier 4 keeps ten claim lines at
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
    out = build_context(doc)
    assert len(out) <= 30_000
    assert "<" not in out
    for claim in doc.claims:
        # The bracket form, so echoed context can never be read back as a claim prefix.
        assert f"[{claim.number}] " in out
        for block in claim.blocks:
            assert block.html.split("<")[0][:60] in out
    assert [len(c.blocks) for c in doc.claims] == blocks_per_claim


def test_build_context_hard_cuts_a_pathological_document() -> None:
    """T10 — tier 5, the only tier that is a guarantee.

    NOTE: reaching tier 5 needs a claim with many paragraphs, not one enormous one.
    PLAN §15.6's "200 KB single-claim document" is bounded by tier 4 (which caps the
    first block at 600 characters) and never reaches the hard cut, so it cannot assert
    the tail. Both shapes are asserted here -- the bound holds either way.
    """
    many_blocks = (
        "<h1>Claims</h1><p>1. start</p>"
        + "".join(f"<p>{'y' * 200}</p>" for _ in range(1_000))
        + "<p>2. b</p>"
    )
    out = build_context(parse(many_blocks))
    assert len(out) == 30_000
    assert out.endswith(CONTEXT_TAIL)

    one_huge = "<h1>Claims</h1><p>1. " + "x" * 200_000 + "</p><p>2. b</p>"
    assert len(build_context(parse(one_huge))) <= 30_000


def test_claims_excerpt_selects_and_never_truncates() -> None:
    """The view every generating node reads. Truncating the text a node is about to
    rewrite is the defect the live pre-flight found (PLAN §20.7 failure A)."""
    doc = parse(SEED_1)
    out = claims_excerpt(doc, [5, 2])
    assert out.splitlines()[0] == "RELEVANT CLAIMS, IN FULL"
    assert out.index("[2] ") < out.index("[5] ")  # filtered against the parse, sorted
    assert doc.claims[1].blocks[0].html in out  # in full


@pytest.mark.parametrize("numbers", [[], [99], [0, 400]])
def test_claims_excerpt_is_empty_for_unknown_claims(numbers: list[int]) -> None:
    """The caller omits the block entirely rather than emitting a bare header."""
    assert claims_excerpt(parse(SEED_1), numbers) == ""
