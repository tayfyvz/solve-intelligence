"""The three plain-text views of a document, and the retrieval that scopes the largest.

Nothing here needs an API key: `build_outline` and `build_context` are pure functions.
"""

import re

import pytest

from app.ai.document import parse
from app.ai.outline import (
    CLIPPED_MARK,
    CONTEXT_TAIL,
    OUTLINE_HEADER,
    UNTITLED_SECTION,
    build_context,
    build_outline,
    build_spec,
    claims_excerpt,
    content_tokens,
    section_excerpt,
    sections,
    stem,
)
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content

FILLER = (
    "The apparatus described herein may be fabricated by any conventional means known to "
    "those of ordinary skill in the art, and the following description is illustrative "
    "rather than limiting in any respect whatsoever. "
) * 4

# Each section gets its own vocabulary, as a real patent does. Without that the ranking
# has nothing to rank on and a green test would prove only that document order works.
SECTION_HEADINGS = {
    "FIELD OF THE INVENTION": "extracorporeal circulation cardiopulmonary bypass",
    "BACKGROUND": "hollow fibre bundles historically suffered plasma leakage",
    "SUMMARY OF THE INVENTION": "reduced footprint improved gas transfer efficiency",
    "BRIEF DESCRIPTION OF THE DRAWINGS": "figure schematic cross-section exploded isometric view",
    "DETAILED DESCRIPTION": "manifold housing potting compound annular gasket tolerance",
}

# The planted fact, deliberately in the LAST section three quarters of the way down. A
# context packed in document order would never reach it, so finding it is a statement
# about the ranking rather than about the first 30,000 characters.
KEY_FACT = (
    "The priming volume of the earlier oxygenator is 220 millilitres, which is unsafe for neonates."
)

# The budget these tests exercise. The shipped default is large enough that a whole
# 37-page patent fits and no retrieval happens at all, so it is pinned explicitly here: a
# test of the ranker that never runs the ranker asserts nothing.
TIGHT = 30_000


def long_patent(*, claims: int = 60, paragraphs: int = 18) -> str:
    """A patent at the scale that makes retrieval necessary: ~110,000 characters, 60
    claims, 90 description paragraphs. Built here rather than committed as a fixture so
    the shape is visible in the test that depends on it."""
    parts: list[str] = []
    for heading, words in SECTION_HEADINGS.items():
        parts.append(f"<h1>{heading}</h1>")
        for i in range(paragraphs):
            # Padded to the length of its neighbours on purpose: a short paragraph is
            # cheap, and the greedy pack would pick it up with the last of any budget.
            body = f"{words} {FILLER} ({i})"
            if heading == "DETAILED DESCRIPTION" and i == 13:
                body = f"{FILLER} {KEY_FACT}"
            parts.append(f"<p>{body}</p>")
    parts.append("<h1>Claims</h1>")
    for n in range(1, claims + 1):
        tail = "" if n == 1 else f" of claim 1, wherein the substrate is of type {n}"
        parts.append(f"<p>{n}. A microfluidic oxygenator{tail} comprising a membrane.</p>")
    return "".join(parts)


def ask(question: str, html: str = "", *, max_chars: int = TIGHT):
    """Exactly what `nodes._retrieve` does on the answer branch, so a green test here is
    a statement about the running system rather than about a helper."""
    doc = parse(html or long_patent())
    return build_context(doc, content_tokens(question), max_chars=max_chars)


def claim_lines(outline: str) -> list[str]:
    return [line for line in outline.splitlines() if line.startswith("  ")]


def test_build_outline_maps_the_document_and_its_ladder_bottoms_out() -> None:
    """The planner's view: one line per claim, plain text, and multi-paragraph claims
    said out loud. Four tiers, each evaluated only if the previous result is too long —
    never a loop, so the same document always produces the same string."""
    out = build_outline(parse(SEED_1))
    assert out.splitlines()[0] == OUTLINE_HEADER
    assert len(claim_lines(out)) == 8
    assert "<" not in out  # the model must not start thinking in markup
    assert "[+4 more paragraphs]" in out  # claim 1 spans five

    filler = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do " * 7
    sixty = "<h1>Claims</h1>" + "".join(f"<p>{n}. {filler}</p>" for n in range(1, 61))
    doc = parse(sixty)
    assert len(claim_lines(build_outline(doc, max_chars=100_000))) == 60  # tier 1 fits

    windowed = build_outline(doc, max_chars=1_600)
    lines = claim_lines(windowed)
    assert len(windowed) <= 1_600
    assert len(lines) == 21  # 10 + the omitted-range line + 10
    assert [line for line in lines if "omitted" in line] == ["  … (claims 11–50 omitted) …"]
    for line in lines:
        if "omitted" not in line:  # tier 3 was reached: 60 characters of claim text
            assert len(re.sub(r"^\s+\d+[.)] ", "", line).rstrip("…")) <= 60
    # The ladder bottoms out rather than looping or shrinking further.
    assert build_outline(doc, max_chars=1) == windowed


def test_build_context_carries_a_whole_document_when_it_fits() -> None:
    """Tier 1, which is the shipped configuration for every realistic patent: every claim
    in full, nothing omitted, and therefore no warning for the user to read. That silence
    is the feature — a limitation warning under every answer is one nobody reads."""
    doc = parse(SEED_1)
    view = build_context(doc, max_chars=200_000)

    assert "<" not in view.text
    for claim in doc.claims:
        # The bracket form, so echoed context can never be read back as a claim prefix.
        assert f"[{claim.number}] " in view.text
        for block in claim.blocks:
            assert block.html.split("<")[0][:60] in view.text
    assert view.omitted == () and view.omitted_paragraphs == 0
    assert "--- NOT SHOWN IN FULL ---" not in view.text
    assert "You were shown" not in view.text


def test_retrieval_finds_the_relevant_paragraph_and_is_deterministic() -> None:
    """The repro this exists for: on a patent four times too big for the budget, a
    context packed in document order stops long before the planted fact. Asking about it
    must put it in front of the model — and asking about something else must not, which
    is what makes this a statement about ranking rather than about luck.

    The ranking key is a total order and the tiers are evaluated rather than looped, so
    the same question gives the same bytes.
    """
    view = ask("what is the priming volume of the prior art oxygenator?")
    assert KEY_FACT in view.text
    assert len(view.text) <= TIGHT
    assert KEY_FACT not in ask("what do the drawings show?").text

    assert ask("what do the drawings show?").text == ask("what do the drawings show?").text
    assert view.text != ask("what do the drawings show?").text


def test_what_could_not_be_shown_is_named_never_silently_dropped() -> None:
    """The context says what it left out, and hands the caller the labels so the USER can
    be told the same thing. Every label is a real heading, so the sentence they read
    names something they can actually type back.

    The counts are stated too: a model asked "how many embodiments are described?" counts
    what is in front of it and does not experience that as guessing.
    """
    view = ask("what is the priming volume?")
    assert view.omitted and view.omitted_paragraphs > 0
    assert "--- NOT SHOWN IN FULL ---" in view.text
    for label in view.omitted:
        assert label in view.text
    assert set(view.omitted) <= {s.label for s in sections(parse(long_patent()))}

    total = sum(len(s.blocks) for s in sections(parse(long_patent())))
    shown = total - view.omitted_paragraphs
    assert f"You were shown {shown} of this document's {total} description paragraphs." in view.text
    assert 0 < shown < total

    # A claims-only document is never told it has zero paragraphs, which would invite the
    # model to call it empty while holding twenty claims.
    claims_only = "<h1>Claims</h1>" + "".join(f"<p>{i}. {'x' * 700}</p>" for i in range(1, 60))
    bare = build_context(parse(claims_only), max_chars=4_000)
    assert bare.omitted and "description paragraphs" not in bare.text


def test_a_question_that_matches_nothing_spreads_across_sections_and_says_so() -> None:
    """The worst failure, because it was silent. When the question's words appear nowhere
    — a vocabulary mismatch, or any "summarise this" — the old fallback was document
    order, which answered "summarise this patent" from the front of the first section and
    omitted the section literally called Summary."""
    view = ask("summarise this patent")
    assert view.matched is False
    assert {h for h in SECTION_HEADINGS if f"## {h}" in view.text} == set(SECTION_HEADINGS)

    assert ask("what is the priming volume of the earlier oxygenator?").matched is True


def test_the_budget_is_a_guarantee_for_any_document() -> None:
    """Four shapes that each break a different tier, and the bound holds for all of them.

    The last tier cuts the BODY and re-attaches the manifest rather than cutting the
    string end to end: a blind byte cut severed the "NOT SHOWN IN FULL" block on exactly
    the documents where it matters most.
    """
    # A document with no headings at all — a .txt import — is still one nameable section.
    headingless = "".join(f"<p>{FILLER} {i}</p>" for i in range(200))
    view = build_context(parse(headingless), content_tokens("priming volume"), max_chars=TIGHT)
    assert view.omitted == (UNTITLED_SECTION,) and view.headed is False

    # A single paragraph bigger than the whole budget is CLIPPED, not skipped: skipping it
    # drops the only paragraph mentioning the question and then advises the user to ask
    # about that section, which provably cannot work.
    giant = "The fill volume is 220 millilitres. " + ("padding " * 6_000)
    one_huge_paragraph = (
        f"<h1>DETAILED DESCRIPTION</h1><p>{giant}</p><h1>Claims</h1><p>1. A.</p><p>2. B.</p>"
    )
    clipped = build_context(
        parse(one_huge_paragraph), content_tokens("fill volume millilitres"), max_chars=TIGHT
    )
    assert "The fill volume is 220 millilitres." in clipped.text
    assert CLIPPED_MARK in clipped.text and clipped.omitted == ("DETAILED DESCRIPTION",)

    # A claim-heavy document: without windowing the claim LIST there is no tier in which
    # claims yield to prose, so a question about the Background is answered from 289
    # claims and nothing else.
    claim_heavy = (
        "<h1>BACKGROUND</h1>"
        + "".join(f"<p>The priming volume matters here {i}. {'pad ' * 50}</p>" for i in range(40))
        + "<h1>Claims</h1>"
        + "".join(f"<p>{n}. {'z' * 90}</p>" for n in range(1, 901))
    )
    windowed = build_context(
        parse(claim_heavy),
        content_tokens("what does the background say about priming volume?"),
        max_chars=TIGHT,
    )
    assert "## BACKGROUND" in windowed.text and "priming volume matters" in windowed.text
    # Labelled by COUNT, never by the numbers at the window edges: the parser records
    # claim numbers verbatim, so edge labels read "claims 3-3" on a document with
    # duplicates. The ends survive, where the independent and newest claims live.
    assert any("claims in the middle of the claim list" in label for label in windowed.omitted)
    assert "[1] " in windowed.text and "[900] " in windowed.text

    # And the hard cut, which is the only tier that is a guarantee.
    many_blocks = (
        "<h1>Claims</h1><p>1. start</p>"
        + "".join(f"<p>{'y' * 200}</p>" for _ in range(1_000))
        + "<p>2. b</p>"
    )
    cut = build_context(parse(many_blocks), max_chars=TIGHT)
    assert CONTEXT_TAIL in cut.text
    assert cut.text.rstrip().endswith("Never guess at it.")  # the manifest always survives

    for bounded in (view, clipped, windowed, cut):
        assert len(bounded.text) <= TIGHT


def test_the_stemmer_folds_inflections_without_merging_different_words() -> None:
    """The vocabulary gap, narrowed. A user asking about "volumes" against a patent that
    says "volume" scored zero — the same silent miss as a true synonym with none of its
    excuse. `-er`/`-ly`/`-est` were tried and removed: one suffix family managed a false
    match and a miss at the same time.

    Stopwords are subtracted BEFORE stemming. The other order deletes any content word
    whose stem collides with a stopword's — "shoulder" folds to "should" — so a question
    about the shoulder of a housing came back "none of the words in your question appear
    in this document" with the word sitting right there.
    """
    for group in [
        ("filter", "filters", "filtering", "filtered"),
        ("assembly", "assemblies"),
        ("gas", "gases"),
        ("volume", "volumes"),
        ("lens", "lenses"),
        ("process", "processes"),
    ]:
        assert len({stem(w) for w in group}) == 1, group

    for a, b in [("prime", "primer"), ("number", "numb"), ("base", "basis"), ("less", "lesser")]:
        assert stem(a) != stem(b), (a, b)

    for word in ["shoulder", "theme", "wither", "thane", "theirs"]:
        assert content_tokens(word) == {stem(word)}
    assert content_tokens("what is the shoulder of the housing?") == {
        stem("shoulder"),
        stem("housing"),
    }

    # A stem is a sort key: it never reaches the model, the document or a citation.
    view = ask("what is the priming volume?")
    assert re.search(r"\bvolum\b", view.text) is None
    assert re.search(r"\bpriming\b", view.text) is not None


def test_the_generating_views_carry_full_text_or_nothing() -> None:
    """Truncating the very text a node is about to rewrite is what made a model refuse
    and ask to be shown the claim. An unmatched selection returns "" so the caller omits
    the block rather than emitting a bare header."""
    doc = parse(SEED_1)
    excerpt = claims_excerpt(doc, [5, 2])
    assert excerpt.splitlines()[0] == "RELEVANT CLAIMS, IN FULL"
    assert excerpt.index("[2] ") < excerpt.index("[5] ")  # filtered against the parse, sorted
    assert doc.claims[1].blocks[0].html in excerpt
    for numbers in ([], [99], [0, 400]):
        assert claims_excerpt(doc, numbers) == ""

    appendix = parse(
        "<h2>Appendix</h2><p>Inception. The Matrix.</p><h1>Claims</h1><p>1. A widget.</p>"
    )
    section = section_excerpt(appendix, "appendix")  # matched case-insensitively
    assert section.splitlines()[0] == "RELEVANT SECTION, IN FULL — Appendix"
    assert "Inception. The Matrix." in section
    for heading in (None, "", "No Such Section"):
        assert section_excerpt(appendix, heading) == ""


@pytest.mark.parametrize(
    "html",
    [
        # A hand-typed section after the claims is folded into claim 1's continuation.
        "<p>Field.</p><h1>Claims</h1><p>1. A device.</p>"
        "<p><strong>Details</strong></p><p>Round and blue.</p>",
        # …and one between two real headings merges into the previous section's body.
        "<h2>Background</h2><p>Background text.</p>"
        "<p><strong>Details</strong></p><p>Round and blue.</p>"
        "<h1>Claims</h1><p>1. A device.</p>",
    ],
)
def test_a_hand_typed_pseudo_heading_is_named_in_the_outline(html: str) -> None:
    """A user adding a section rarely reaches for the H1/H2/H3 buttons — they select a
    short line and press Cmd+B, which parses as a `<p>` with a bold mark and no heading
    tag. The outline is the only thing `understand` reads, and its rule is "the outline
    does not list it" means "the document does not contain it", so `understand`
    dead-ended on questions about text that was right there.

    The fix names the words without moving them: `build_context` already retrieved the
    content correctly, which is why nothing about the parse changes.
    """
    doc = parse(html)
    out = build_outline(doc)
    assert '"Details"' in out and "reads as its own heading" in out

    view = build_context(doc, {"detail"})
    assert "Details" in view.text and "round and blue" in view.text.lower()

    # The heuristic's bound, from both sides. Only a SHORT, UNPUNCTUATED, wholly-bold
    # line reads as a hand-typed section name — and a real multi-paragraph claim, which
    # Patent 1 has, must never start being reported as a mislabelled section.
    bold_sentence = parse(
        "<h1>Claims</h1><p>1. A device.</p>"
        "<p><strong>The widget is round and blue in every embodiment described here.</strong></p>"
    )
    assert "reads as its own heading" not in build_outline(bold_sentence)
    assert "reads as its own heading" not in build_outline(parse(SEED_1))


# ------------------------------------------------------ build_spec, for the edit branch


def test_build_spec_shows_the_whole_specification_and_none_of_the_claims() -> None:
    """The editing branch's view: every description paragraph, no claim text.

    Both halves matter. The whole body is what lets `plan_ops` copy a `replace_text.find`
    string that actually matches — reconstructing one from memory matches nothing and
    edits nothing, silently. Excluding the claims is what keeps that affordable: every
    caller already holds `claims_excerpt`, so rendering them here would send the claims
    twice and spend the budget on text the model is already looking at.
    """
    doc = parse(long_patent())
    view = build_spec(doc, content_tokens("priming volume"))

    assert view.omitted == ()  # 82k of body, well inside the shipped 200k budget
    assert KEY_FACT in view.text
    for heading in SECTION_HEADINGS:
        assert f"## {heading}" in view.text
    # No claim survives into this view, by text and by the bracket prefix it renders with.
    assert "A microfluidic oxygenator" not in view.text
    assert "[1] " not in view.text


def test_build_spec_is_question_independent_while_the_whole_body_fits() -> None:
    """Tier 1 never consults the ranking, and that is a caching property, not a detail.

    The prompt prefix is only reused across turns if it is byte-identical across turns.
    A view that re-ranked on every question would move the prefix every time and throw
    away the ~99% cache hit the whole-document design was measured to get.
    """
    doc = parse(long_patent())
    asked = build_spec(doc, content_tokens("what is the priming volume of the oxygenator"))
    unrelated = build_spec(doc, content_tokens("aardvark zebra xylophone"))
    assert asked.text == unrelated.text

    # Below the budget it MUST become question-scoped — that is the whole point of the
    # second tier — so the property above is a statement about tier 1, not about luck.
    tight = build_spec(doc, content_tokens("priming volume"), max_chars=8_000)
    other = build_spec(doc, content_tokens("aardvark zebra xylophone"), max_chars=8_000)
    assert tight.text != other.text
    assert KEY_FACT in tight.text  # the ranker found the planted fact...
    assert other.matched is False  # ...and says so when it had nothing to rank on


def test_build_spec_names_what_it_dropped_and_respects_its_budget() -> None:
    """Invariant 11 on the editing branch: anything not shown is NAMED, never silently
    dropped. Without this, a plan built from a quarter of a document looks exactly like
    one built from all of it."""
    doc = parse(long_patent())
    view = build_spec(doc, content_tokens("priming volume"), max_chars=8_000)

    assert len(view.text) <= 8_000
    assert view.omitted  # sections it could not fit, named
    assert "--- NOT SHOWN IN FULL ---" in view.text
    assert "You have NOT been shown all of:" in view.text
    assert re.search(r"\[… \d+ paragraphs? not shown here …\]", view.text)


def test_build_spec_is_empty_for_a_document_that_is_only_claims() -> None:
    """Both seed patents are pure claim sets, so this is the common case and not an edge
    one. "" lets the caller omit the block; a header with "(none)" under it twice is
    noise the model has to reason about on every single edit."""
    assert build_spec(parse(SEED_1)).text == ""
    assert build_spec(parse("<h1>Claims</h1><p>1. A widget.</p>")).text == ""
