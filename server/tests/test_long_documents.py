"""L-tests — a question about ANY part of a patent too long to send whole.

The defect these exist for, reproduced end to end: on a 37-page patent the Q&A branch
could see only the claims, and the description was replaced by an internal placeholder
that the model then quoted back. The verifier caught it, correctly, and the user was told
the AI had quoted text "not in this document" — an accusation about a string this program
had written itself, and nothing they could act on.

Nothing here needs an API key: `build_context` is a pure function and the graph tests
inject a fake answerer.
"""

from __future__ import annotations

import pytest

from app.ai.document import parse
from app.ai.graph import GraphInput
from app.ai.outline import (
    CLIPPED_MARK,
    CONTEXT_TAIL,
    OMITTED_MARK,
    STOPWORDS,
    UNTITLED_SECTION,
    build_context,
    sections,
    tokens,
)
from app.ai.schemas import Answer, Citation
from app.ai.verify import (
    SCAFFOLD_RE,
    W_NO_MATCH,
    W_PARTIAL_CONTEXT,
    W_PARTIAL_NO_HEADINGS,
    W_QUOTED_SCAFFOLD,
    W_UNVERIFIED_QUOTE,
    check_citations,
    partial_context_warning,
)
from tests.fakes import Recorder, fake_bundle, run_terminal, understanding

# The planted fact. It is the thing a user asks about and the thing the old code made
# unreachable, because the whole description was replaced by one placeholder.
KEY_FACT = (
    "The priming volume of the earlier oxygenator is 220 millilitres, which is unsafe for neonates."
)

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

# DELIBERATELY in the LAST section, three quarters of the way down. A context packed in
# document order would never reach it, so L2 passing is a statement about the ranking and
# not about the first 30,000 characters happening to contain the answer.
KEY_FACT_SECTION = "DETAILED DESCRIPTION"
KEY_FACT_INDEX = 13


def long_patent(*, claims: int = 60, paragraphs: int = 18) -> str:
    """A patent at the scale that broke this: ~110,000 characters, 60 claims, ~90
    description paragraphs. Built here rather than committed as a fixture so the shape is
    visible in the test that depends on it."""
    parts: list[str] = []
    for heading, words in SECTION_HEADINGS.items():
        parts.append(f"<h1>{heading}</h1>")
        for i in range(paragraphs):
            # Padded to the length of its neighbours on purpose. A short paragraph is
            # cheap and the greedy pack picks it up with the last of any budget, which
            # would make L2 pass without the ranking doing any work at all.
            body = f"{words} {FILLER} ({i})"
            if heading == KEY_FACT_SECTION and i == KEY_FACT_INDEX:
                body = f"{FILLER} {KEY_FACT}"
            parts.append(f"<p>{body}</p>")
    parts.append("<h1>Claims</h1>")
    for n in range(1, claims + 1):
        tail = "" if n == 1 else f" of claim 1, wherein the substrate is of type {n}"
        parts.append(f"<p>{n}. A microfluidic oxygenator{tail} comprising a membrane.</p>")
    return "".join(parts)


# The RETRIEVAL budget these tests exercise. The shipped default is 120,000 — big enough
# that a whole 37-page patent fits and no retrieval happens at all, which is the point of
# L16. Everything else here is about what happens ABOVE the budget, a range that is
# reachable by design (`max_html_chars` is 200,000), so it is pinned explicitly rather
# than inherited: a test of the ranker that never runs the ranker asserts nothing.
TIGHT = 30_000


def ask(question: str, html: str = "", *, max_chars: int = TIGHT):
    """Exactly what `nodes._retrieve` does for the answer branch, so a green test here is
    a statement about the running system and not about a helper."""
    doc = parse(html or long_patent())
    return build_context(doc, tokens(question) - STOPWORDS, max_chars=max_chars)


# --------------------------------------------------------------- the reported defect


def test_L1_the_internal_placeholder_is_gone() -> None:
    """L1 — the exact leak. `(omitted — 89 paragraphs)` was a sentence the model could
    read as prose and quote as the document's own words. No context may contain it."""
    for question in ("what is the priming volume?", "summarise this", "how many claims?"):
        assert "(omitted —" not in ask(question).text


def test_L2_a_question_about_the_description_reaches_the_description() -> None:
    """L2 — the repro, answered. The planted fact is buried in the LAST section of a
    document four times too big for the budget; a context packed in document order stops
    long before it. Asking about it must still put it in front of the model."""
    view = ask("what is the priming volume of the prior art oxygenator?")
    assert KEY_FACT in view.text
    assert len(view.text) <= TIGHT

    # The control: a question about something else does NOT drag it in, which is what
    # makes the assertion above a statement about retrieval rather than about luck.
    assert KEY_FACT not in ask("what do the drawings show?").text


def test_L3_what_was_left_out_is_named_not_dropped() -> None:
    """L3 — "must not silently drop content". The context says so, and the caller is
    handed the labels so the USER can be told the same thing."""
    view = ask("what is the priming volume?")
    assert view.omitted  # this document cannot fit; something had to go
    assert view.omitted_paragraphs > 0
    assert "--- NOT SHOWN IN FULL ---" in view.text
    for label in view.omitted:
        assert label in view.text
    # Every named label is a real heading of this document, so the sentence the user reads
    # tells them something they can actually type back.
    headings = {s.label for s in sections(parse(long_patent()))}
    assert set(view.omitted) <= headings


def test_L4_degradation_is_deterministic() -> None:
    """L4 — "the same document and question produce the same context". The ranking key is
    a total order and the tiers are evaluated, never looped, so this is exact equality and
    not a similarity check."""
    question = "what do the drawings show?"
    first, second = ask(question), ask(question)
    assert first.text == second.text
    assert first.omitted == second.omitted
    # A DIFFERENT question legitimately produces a different context — that is the feature.
    assert ask("what is the priming volume of the prior art?").text != first.text


def test_L5_bounded_at_the_two_hundred_thousand_character_ceiling() -> None:
    """L5 — `max_html_chars` is 200,000, so that is the largest document that can ever
    reach this code. Above it the route 413s before parsing (asserted in test_ai_routes)."""
    html = long_patent(claims=200, paragraphs=44)
    assert len(html) > 200_000
    view = build_context(parse(html), tokens("priming volume") - STOPWORDS, max_chars=TIGHT)
    assert len(view.text) <= TIGHT
    assert view.omitted


# ------------------------------------------------------------------- the section split


def test_L6_sections_split_on_headings() -> None:
    doc = parse(long_patent())
    found = sections(doc)
    assert [s.heading for s in found] == list(SECTION_HEADINGS)
    assert all(not s.after_claims for s in found)  # everything here precedes the claims
    assert all(len(s.blocks) == 18 for s in found)


def test_L7_a_document_with_no_headings_is_still_one_nameable_section() -> None:
    """The awkward real case: a .txt import with no section headings at all. Without a
    label there is nothing to tell the user to ask about, so one is supplied."""
    html = "".join(f"<p>{FILLER} {i}</p>" for i in range(200))
    found = sections(parse(html))
    assert len(found) == 1
    assert found[0].heading == ""
    assert found[0].label == UNTITLED_SECTION

    view = build_context(parse(html), tokens("priming volume") - STOPWORDS, max_chars=TIGHT)
    assert view.omitted == (UNTITLED_SECTION,)
    assert view.headed is False  # nothing to "ask about by name"
    assert OMITTED_MARK.format(n=1, s="") in view.text or "not shown here …]" in view.text


def test_L8_postamble_sections_are_reachable_too() -> None:
    """An Abstract after the claims is a section like any other — it must be rankable, not
    special-cased into oblivion the way the old tier 2 dropped the whole postamble."""
    html = long_patent() + "<h1>ABSTRACT</h1><p>An oxygenator with a low priming volume.</p>"
    view = build_context(
        parse(html), tokens("abstract priming volume") - STOPWORDS, max_chars=TIGHT
    )
    assert "An oxygenator with a low priming volume." in view.text


# ------------------------------------------------ the verifier, still verifying


def test_L9_scaffold_regex_matches_what_outline_actually_emits() -> None:
    """VF-SCAFFOLD. `verify.py` deliberately does not import `outline.py`, so the two
    agree by test rather than by import. If the marker's shape changes, this fails."""
    assert SCAFFOLD_RE.search(OMITTED_MARK.format(n=12, s="s"))
    assert SCAFFOLD_RE.search(OMITTED_MARK.format(n=1, s=""))


def test_L10_a_quoted_placeholder_gets_the_actionable_sentence() -> None:
    """L10 — D1 itself. The verifier must still CATCH the quote; only what the user is
    told changes. The old message named an internal string and offered no next step."""
    doc = parse(long_patent())
    marker = OMITTED_MARK.format(n=17, s="s")
    warnings = check_citations(doc, [("section", "Sections before the claims", marker)])
    assert warnings == [W_QUOTED_SCAFFOLD]
    assert "paragraph" not in warnings[0]  # no internal counts leak
    assert "Ask about a specific section by name" in warnings[0]


def test_L11_a_genuine_hallucination_still_gets_the_old_message() -> None:
    """The behaviour that "must be preserved". A quote that is neither in the document nor
    one of our markers is the model inventing text, and it is still called that."""
    doc = parse(long_patent())
    warnings = check_citations(doc, [("claim", "3", "a titanium housing sealed with epoxy")])
    assert warnings == [
        W_UNVERIFIED_QUOTE.format(quote="a titanium housing sealed with epoxy", where="claim 3")
    ]


def test_L12_repeated_placeholder_quotes_say_it_once() -> None:
    doc = parse(long_patent())
    marker = OMITTED_MARK.format(n=3, s="s")
    cites = [("section", "BACKGROUND", marker), ("section", "SUMMARY", marker)]
    assert check_citations(doc, cites) == [W_QUOTED_SCAFFOLD]


@pytest.mark.parametrize("omitted", [[], [""], ["", ""]])
def test_L13_nothing_omitted_means_no_warning(omitted: list[str]) -> None:
    """The common case — a normal patent fits whole — must stay silent. A warning on every
    answer is a warning nobody reads."""
    assert partial_context_warning(omitted) == []


def _answer_run(html: str, question: str, quote: str, **settings_kwargs):
    """Drive the REAL graph with a fake answerer and return the terminal state."""
    answer = Answer(
        text="It is 220 millilitres.",
        citations=[Citation(kind="section", ref="DETAILED DESCRIPTION", quote=quote)],
    )
    return run_terminal(
        # NOT "prior art" — that phrase makes `missing_file_question` fire deterministically
        # when no file is attached, and the run never reaches the answer branch at all.
        GraphInput(html=html, instruction=question),
        fake_bundle(
            rec=Recorder(),
            understand=understanding(intent="answer", target_kind="section", claim_numbers=[]),
            answer=answer,
        ),
    )


def test_L15_the_shipped_budget_reads_a_whole_patent_and_says_nothing(ai_settings) -> None:
    """L15 — the headline. At the SHIPPED budget a 37-page patent fits in one call, so
    retrieval never fires and the user is not warned about anything.

    That silence is the feature: a limitation warning on every answer is a warning nobody
    reads, and the whole point of a 120,000-character budget is that the model reads the
    document rather than a selection from it. Measured, not assumed — see `config.py`.
    """
    ai_settings()  # the shipped defaults
    html = long_patent()
    assert len(html) < 120_000
    terminal = _answer_run(html, "what is the priming volume of the earlier oxygenator?", KEY_FACT)

    assert terminal["status"] == "answer"
    assert terminal["warnings"] == []


def test_L15b_a_document_above_the_budget_tells_the_user_end_to_end(ai_settings) -> None:
    """L15b — `omitted_sections` is worth nothing if it stops at the `Retrieved` model, so
    this drives the real graph and reads the terminal warnings the route puts on the wire.
    A fake answerer, no key, no network.

    The budget is squeezed rather than the fixture inflated: what is under test is the
    behaviour ABOVE the budget, which `max_html_chars = 200_000` makes reachable by design.
    """
    ai_settings(max_answer_context_chars=TIGHT)
    terminal = _answer_run(
        long_patent(), "what is the priming volume of the earlier oxygenator?", KEY_FACT
    )

    assert terminal["status"] == "answer"
    # The quote is real, so it is NOT reported as unverified — the verifier still works.
    assert not any("not in this document" in w for w in terminal["warnings"])
    # And the honest limitation is there, naming a section the user can ask about next.
    (partial,) = [w for w in terminal["warnings"] if w.startswith("This document is too long")]
    assert "SUMMARY OF THE INVENTION" in partial or "FIELD OF THE INVENTION" in partial


def test_L14_the_partial_context_warning_names_sections_and_caps_the_list() -> None:
    labels = [f"SECTION {i}" for i in range(9)]
    (message,) = partial_context_warning(labels)
    assert message.startswith(W_PARTIAL_CONTEXT.split("{")[0])
    assert '"SECTION 0"' in message
    assert "and 4 more" in message  # 5 named, 4 counted
    assert "SECTION 8" not in message


# ------------------------------------------------- what the adversarial audit found
#
# Six findings, each with a reproduction. They matter ABOVE the 120,000-char budget,
# which `max_html_chars = 200_000` makes reachable by design — so each one is pinned at a
# tight budget, where the retrieval it is about actually runs.


def test_L16_the_heading_lines_are_charged_to_the_budget() -> None:
    """L16 (audit finding 1) — the packer charged for paragraphs but not for the `##`
    heading lines the renderer emits per surviving section, so EVERY packing tier
    overshot by the same amount. Trimming the claims in tiers 3 and 4 handed the freed
    bytes straight back, so the document fell through to tier 5's hard cut regardless —
    the two tiers that exist to avoid the cut could never reach it.
    """
    html = (
        "".join(f"<h1>SECTION {i}</h1><p>{'word ' * 120}({i})</p>" for i in range(120))
        + "<h1>Claims</h1>"
        + "".join(f"<p>{n}. A device of type {n}.</p>" for n in range(1, 61))
    )
    view = build_context(parse(html), tokens("device") - STOPWORDS, max_chars=TIGHT)

    assert len(view.text) <= TIGHT
    # Tier 2 or 3 handled it, so the manifest is a real one rather than a hard cut.
    assert CONTEXT_TAIL not in view.text
    assert "--- NOT SHOWN IN FULL ---" in view.text


def test_L17_the_top_ranked_paragraph_is_clipped_never_skipped() -> None:
    """L17 (audit finding 3) — one Detailed Description paragraph bigger than the whole
    budget used to be SKIPPED by the greedy rule, so the only paragraph mentioning the
    question was dropped, the budget went to prose that scored zero, and the user was told
    to "ask about that section by name" — advice that provably could not work, because
    asking again produced the same skip. It is clipped instead.
    """
    giant = "The fill volume is 220 millilitres. " + ("padding " * 6_000)
    html = (
        f"<h1>DETAILED DESCRIPTION</h1><p>{giant}</p><h1>Claims</h1><p>1. A device.</p><p>2. B.</p>"
    )
    view = build_context(
        parse(html), tokens("fill volume millilitres") - STOPWORDS, max_chars=TIGHT
    )

    assert "The fill volume is 220 millilitres." in view.text
    assert CLIPPED_MARK in view.text
    assert len(view.text) <= TIGHT
    # Still reported as not shown in full — it was clipped, not shown whole.
    assert view.omitted == ("DETAILED DESCRIPTION",)


def test_L18_a_question_that_matches_nothing_spreads_and_says_so() -> None:
    """L18 (audit finding 2) — the worst failure, because it was silent. When the question's
    words appear nowhere, the old fallback was document order, which answered "summarise
    this patent" from the front of the first section and omitted the section literally
    called Summary. It was also byte-identical to asking nothing at all, and the warning
    ("I did not see all of X") implied the parts shown had been *selected*.
    """
    view = ask("summarise this patent")
    assert view.matched is False

    seen = {h for h in SECTION_HEADINGS if f"## {h}" in view.text}
    # Round-robin: every section contributes its opening paragraphs, so a summary request
    # sees the whole shape of the document instead of the first section and a half.
    assert seen == set(SECTION_HEADINGS)

    (no_match, partial) = partial_context_warning(
        view.omitted, matched=view.matched, headed=view.headed
    )
    assert no_match == W_NO_MATCH  # first, because it changes what the second one means
    assert partial.startswith("This document is too long")


def test_L19_a_matching_question_does_not_get_the_no_match_warning() -> None:
    """The other half of L18: the sentence must not fire on the common case, or it is
    noise that teaches the user to skip the warnings that matter."""
    view = ask("what is the priming volume of the earlier oxygenator?")
    assert view.matched is True
    assert W_NO_MATCH not in partial_context_warning(
        view.omitted, matched=view.matched, headed=view.headed
    )


def test_L20_naming_a_section_does_not_bury_the_answer_filed_elsewhere() -> None:
    """L20 (audit finding 4) — the heading bonus was `len(words & heading_tokens)`, so it
    scaled with the heading's length. A question naming "the detailed description of the
    preferred embodiments" gave all 40 of its filler paragraphs a score of 4, while the
    paragraph actually containing the answer — filed in the Background — scored 2 and
    never made the cut. Naming a section actively destroyed retrieval.
    """
    html = (
        long_patent()
        .replace(f"<p>{FILLER} {KEY_FACT}</p>", "")
        .replace("<h1>BACKGROUND</h1>", f"<h1>BACKGROUND</h1><p>{FILLER} {KEY_FACT}</p>", 1)
    )
    view = build_context(
        parse(html),
        tokens(
            "what is the priming volume in the detailed description of the preferred embodiments?"
        )
        - STOPWORDS,
        max_chars=TIGHT,
    )
    assert KEY_FACT in view.text


def test_L21_a_document_with_no_headings_gets_followable_advice() -> None:
    """L21 (audit finding 5) — "ask about a section by name" is unfollowable with no
    headings, and a user who tried was PUNISHED: the words in "the opening text (no
    heading)" match nothing, so the retry scored lower than the question they asked first.
    """
    # A question that DOES match, so this isolates the no-headings case from L18's.
    view = build_context(
        parse("".join(f"<p>{FILLER} {i}</p>" for i in range(200))),
        tokens("how is the apparatus fabricated?") - STOPWORDS,
        max_chars=TIGHT,
    )
    assert view.matched is True
    (warning,) = partial_context_warning(view.omitted, matched=view.matched, headed=view.headed)
    assert warning == W_PARTIAL_NO_HEADINGS
    assert "Quote a phrase" in warning
    assert "by name" not in warning  # the advice that cannot be followed here
