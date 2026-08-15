"""The applier, end to end. The four README examples are the acceptance tests.

Every row calls `apply_plan` and asserts on the `ApplyResult` the routes actually
consume, so nothing here exercises a shape production never sees.
"""

import re

import pytest

from app.ai import apply as apply_module
from app.ai.apply import (
    KIND_ORDER,
    W_DANGLING_REF,
    W_DANGLING_REF_IN_TEXT,
    W_DUPLICATE_NUMBER,
    apply_plan,
)
from app.ai.document import parse, render
from app.ai.operations import W_MULTIPLE_HITS, W_NO_ANCHOR, W_NO_CLAIM, W_NO_SECTION
from app.ai.schemas import Op
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content  # 8 claims; claim 1 spans five paragraphs
TWO_CLAIMS_NO_HEADING = "<p>1. First claim text.</p><p>2. Second claim text.</p>"


def bold(n: int) -> Op:
    return Op(kind="format_claim", claim_number=n, mark="bold", enabled=True)


def delete(n: int) -> Op:
    return Op(kind="delete_claim", claim_number=n)


def insert(after: int, text: str) -> Op:
    return Op(kind="insert_claim", after_claim_number=after, text=text)


def applied(html: str, ops: list[Op]) -> str:
    result = apply_plan(html, ops)
    assert result.html is not None, f"verification failed: {result.report.errors}"
    return result.html


def claim_texts(html: str) -> list[str]:
    return [c.blocks[0].html for c in parse(html).claims]


def test_example_1_make_claim_1_bold() -> None:
    """README example 1. Every block of the claim, because claim 1 is five paragraphs and
    bolding only the numbered one is not what anybody means."""
    result = apply_plan(SEED_1, [bold(1)])
    assert result.html is not None
    assert result.html.count("<strong>") == 5
    assert result.warnings == []
    assert result.report.ok
    for claim in parse(SEED_1).claims[1:]:  # claims 2-8 untouched, byte for byte
        assert claim.blocks[0].html in result.html


def test_example_2_delete_claim_3_and_remap_the_cross_references() -> None:
    """README example 2. The renumber runs once and the references follow it — including
    the seed's own error, which is carried faithfully rather than silently corrected:
    claim 7 says "claim 5" where it means 6, and 5 renumbers to 4."""
    out = applied(SEED_1, [delete(3)])
    doc = parse(out)
    assert len(doc.claims) == 7
    assert [c.number for c in doc.claims] == list(range(1, 8))
    assert apply_plan(SEED_1, [delete(3)]).warnings == []

    assert "of claim 3" in doc.claims[3].blocks[0].html  # was 5, referred to claim 4
    assert "of claim 3" in doc.claims[4].blocks[2].html  # was 6
    assert "of claim 4" in doc.claims[5].blocks[0].html  # was 7, the seed's own error
    assert "of claim 5" in doc.claims[6].blocks[0].html  # was 8


def test_example_3_add_a_dependent_claim_after_claim_2() -> None:
    """README example 3, plus the rule that makes it safe anywhere but the end: an
    inserted claim's text was written against the numbering the model was SHOWN, so it is
    remapped along with everything else."""
    text = "The wireless optogenetic device of claim 2, wherein the glass is borosilicate."
    out = applied(SEED_1, [insert(2, text)])
    assert len(parse(out).claims) == 9
    assert "<p>3. The wireless optogenetic device of claim 2," in out
    assert "of claim 5" in out and "of claim 4" not in out

    remapped = applied(SEED_1, [insert(0, "A device according to the method of claim 1.")])
    assert parse(remapped).claims[0].blocks[0].html.endswith("method of claim 2.")

    # The insert cursor: two inserts on one anchor produce 2, AAA, BBB — not 2, BBB, AAA.
    chained = claim_texts(applied(SEED_1, [insert(2, "AAA."), insert(2, "BBB.")]))
    assert chained[2] == "AAA." and chained[3] == "BBB."


def test_example_4_write_a_background_section_and_delete_it_again() -> None:
    """README example 4, and the regression it hides: an inserted Background reading
    "1. Field of the Invention" / "2. Description of Related Art" satisfies the >=2
    fallback on the NEXT parse and becomes the claims region — the exact bug the
    three-region model exists to prevent. `insert_section` synthesises a claims heading
    to stop it.

    `delete_section` is the round trip, and it cannot reach the claims: the claims region
    lives in its own field, never in the two lists that operation searches.
    """
    section = Op(
        kind="insert_section",
        heading="Background",
        paragraphs=["1. Field of the Invention", "2. Description of Related Art"],
        position="before_claims",
    )
    out = applied(SEED_1, [section])
    assert out.startswith("<h1>Background</h1>")
    assert out.index("<h1>Claims</h1>") > out.index("<h1>Background</h1>")
    assert len(parse(out).claims) == 8

    after = applied(out, [delete(3)])
    assert "<p>1. Field of the Invention</p>" in after
    assert len(parse(after).claims) == 7

    removed = applied(out, [Op(kind="delete_section", heading="Background")])
    assert "Field of the Invention" not in removed and "Background" not in removed
    assert len(parse(removed).claims) == 8

    refused = apply_plan(SEED_1, [Op(kind="delete_section", heading="Claims")])
    assert refused.html is not None
    assert len(parse(refused.html).claims) == 8
    assert refused.warnings == [W_NO_SECTION.format(heading="Claims")]


def test_claim_references_are_bound_before_anything_mutates() -> None:
    """The whole reason binding happens first.

    Naive: 1 2 3 4 5 6 → delete #3 → 1 2 4 5 6 → delete #5 → deletes old claim 6.
    """
    html = "<h1>Claims</h1>" + "".join(
        f"<p>{n}. Claim number {n} text, distinct.</p>" for n in range(1, 7)
    )
    original = claim_texts(html)

    out = applied(html, [delete(3), delete(5)])
    assert claim_texts(out) == [original[i] for i in (0, 1, 3, 5)]
    assert applied(html, [delete(5), delete(3)]) == out  # order within a kind is irrelevant


def test_the_renumber_runs_exactly_once_and_the_remap_does_not_cascade() -> None:
    """Two mistakes that both produce correct-looking output much of the time.

    A second renumber usually looks fine, and a loop of `str.replace` over the mapping
    double-applies: deleting claim 3 gives the chain 4→3, 5→4, 6→5, 7→6, 8→7, which a
    sequential loop cascades end to end down to 3.
    """
    seen: list[list[int]] = []
    real_render = apply_module.render

    def spy(doc):
        seen.append([c.number for c in doc.claims])
        return real_render(doc)

    apply_module.render = spy
    try:
        applied(SEED_1, [delete(3), delete(5)])
    finally:
        apply_module.render = real_render
    assert seen == [[1, 2, 3, 4, 5, 6]]

    chain = "<h1>Claims</h1>" + "".join(
        f"<p>{n}. A widget as in claim {n + 1}.</p>" for n in range(1, 9)
    )
    targets = [int(m) for m in re.findall(r"claim (\d+)", applied(chain, [delete(3)]))]
    # Two of these are correct oddities: the reference to the DELETED claim 3, left
    # verbatim because we never guess, and the reference to claim 9, which never existed.
    assert targets == [2, 3, 4, 5, 6, 7, 9]
    assert targets != [3] * len(targets)  # what a loop of str.replace produces


def test_dangling_and_duplicate_references_warn_without_blocking() -> None:
    """A reference to a deleted claim is left verbatim and reported, never guessed at,
    and the warning names the referring claim's NEW number — what the user will see when
    they read it. A duplicated claim number warns once, when the table is built."""
    result = apply_plan(SEED_1, [delete(1)])
    assert result.html is not None
    assert len([w for w in result.warnings if "which was deleted" in w]) == 4
    assert result.html.count("of claim 1") == 4  # left verbatim

    three = (
        "<h1>Claims</h1><p>1. A device.</p><p>2. A method.</p>"
        "<p>3. The device of claim 2, being blue.</p>"
    )
    dangling = apply_plan(three, [delete(1), delete(2)]).warnings
    assert W_DANGLING_REF.format(number=1, old=2) in dangling

    duplicates = "<h1>Claims</h1><p>1. a</p><p>2. b</p><p>2. c</p><p>2. d</p><p>3. e</p>"
    dupe_result = apply_plan(duplicates, [])
    assert dupe_result.report.ok
    assert dupe_result.warnings.count(W_DUPLICATE_NUMBER.format(number=2)) == 1
    assert [c.number for c in parse(dupe_result.html or "").claims] == [1, 2, 3, 4, 5]


def test_kind_order_adjacencies_are_load_bearing() -> None:
    """Both plans below are written in the OPPOSITE order to KIND_ORDER, so it is the
    sort that decides rather than the input order.

    (a) replace_claim before format_claim: reversed, replace_claim rebuilds the blocks
        and discards the marks, and the bold is silently lost.
    (b) insert_claim before delete_claim: reversed, the anchor vanishes, the insert is
        skipped, and the user loses a claim with only a warning to show for it.
    """
    from typing import get_args

    from app.ai.schemas import OpKind

    assert set(KIND_ORDER) == set(get_args(OpKind))  # a missing kind is a ValueError

    out = applied(
        SEED_1, [bold(2), Op(kind="replace_claim", claim_number=2, text="A rewritten claim.")]
    )
    assert "<p><strong>2. A rewritten claim.</strong></p>" in out

    result = apply_plan(SEED_1, [delete(3), insert(3, "A broader claim.")])
    assert result.html is not None
    assert len(parse(result.html).claims) == 8
    assert "A broader claim." in claim_texts(result.html)
    assert W_NO_ANCHOR.format(requested=3) not in result.warnings


@pytest.mark.parametrize(
    ("op", "warning"),
    [
        (delete(99), W_NO_CLAIM.format(requested=99)),
        (bold(99), W_NO_CLAIM.format(requested=99)),
        (insert(99, "text"), W_NO_ANCHOR.format(requested=99)),
        (Op(kind="replace_claim", claim_number=99, text="text"), W_NO_CLAIM.format(requested=99)),
        (Op(kind="delete_section", heading="Nope"), W_NO_SECTION.format(heading="Nope")),
    ],
)
def test_an_operation_with_no_target_is_a_no_op_with_one_warning(op: Op, warning: str) -> None:
    """The shared contract of all seven operations: never raise, never guess, leave the
    document alone and say why. A legal document is not a place to guess."""
    result = apply_plan(SEED_1, [op])
    assert result.html == SEED_1
    assert result.warnings == [warning]
    assert result.report.ok


def test_replace_text_is_document_wide_and_cannot_reach_a_claim_number() -> None:
    """The payoff of "the claim number is a field, never text": it is not in any
    `Block.html`, so a document-wide replace is structurally incapable of corrupting it.
    Document-wide is the design, so the count is reported rather than left to be
    discovered later."""
    needle = "wireless optogenetic device"
    expected = SEED_1.count(needle)
    result = apply_plan(SEED_1, [Op(kind="replace_text", find=needle, replace="apparatus")])
    assert result.html is not None
    assert result.warnings == [W_MULTIPLE_HITS.format(count=expected)]

    renumbered = applied(SEED_1, [Op(kind="replace_text", find="claim 1", replace="claim 9")])
    positions = [renumbered.index(f"<p>{n}. ") for n in range(1, 9)]
    assert positions == sorted(positions)  # every prefix present, and still in order
    assert renumbered.count("of claim 9") == 4 and "of claim 1" not in renumbered


@pytest.mark.parametrize(
    "text",
    [
        "A gadget comprising a lever. ",
        " A gadget comprising a lever.",
        "A gadget  comprising a lever.",
        "A gadget\tcomprising a lever.",
        "A gadget comprising a lever.\n",
        "\n    A gadget comprising a lever.",
    ],
)
def test_model_authored_whitespace_produces_the_same_bytes_as_clean_text(text: str) -> None:
    """The highest-severity row here. Written through `escape_text` alone, a trailing
    newline made `render(after) != after_html`, the verifier fired, and a CORRECT plan
    was discarded whole with a sentence naming an internal invariant. Normalising at the
    point of insertion is the fix; weakening the idempotence check is not."""
    clean = "A gadget comprising a lever."
    for op, reference in (
        (insert(2, text), insert(2, clean)),
        (
            Op(kind="replace_claim", claim_number=4, text=text),
            Op(kind="replace_claim", claim_number=4, text=clean),
        ),
        (
            Op(kind="insert_section", heading=text, paragraphs=[text], position="before_claims"),
            Op(kind="insert_section", heading=clean, paragraphs=[clean], position="before_claims"),
        ),
        (
            Op(kind="replace_text", find="lever", replace=text),
            Op(kind="replace_text", find="lever", replace=clean),
        ),
    ):
        assert applied(SEED_1, [op]) == applied(SEED_1, [reference]), op.kind


def test_a_splice_stays_canonical_and_model_markup_becomes_literal_text() -> None:
    """Two things that must be true of anything spliced into a block.

    The splice itself can create whitespace the replacement does not carry: "the device
    of" with "apparatus " gives "the apparatus  of", a run the next parse collapses. So
    the spliced BLOCK is canonicalised — except in `pre`, which `parse()` does not
    collapse either, because a code sample is content and not stray formatting.

    And model-authored text is escaped exactly once, with `quote=False`: the default
    turns `'` into `&#x27;` while TipTap emits the raw apostrophe, so "the system's"
    would flip a byte on the very next round trip and fail the idempotence check on an
    innocent claim.
    """
    out = applied(SEED_1, [Op(kind="replace_text", find="device", replace="apparatus\n")])
    assert "  " not in out and "apparatus" in out

    code = "<pre>let  x = 1;</pre><h1>Claims</h1><p>1. A device.</p><p>2. A method.</p>"
    assert "<pre>const  x = 1;</pre>" in applied(
        code, [Op(kind="replace_text", find="let", replace="const")]
    )

    escaped = applied(SEED_1, [insert(2, 'The system\'s <b>housing</b> holds A & B, "tight".')])
    assert "&#x27;" not in escaped and "&quot;" not in escaped
    assert "&amp;" in escaped and " & " not in escaped
    assert "<b>" not in escaped and "&lt;b&gt;" in escaped
    assert render(parse(escaped)) == escaped


@pytest.mark.parametrize(
    ("op", "missing"),
    [
        (Op(kind="delete_claim"), "claim_number"),
        (Op(kind="format_claim", claim_number=1, mark="bold"), "enabled"),
        (Op(kind="insert_section", heading="H", paragraphs=[]), "position"),
    ],
)
def test_a_malformed_operation_refuses_the_whole_plan(op: Op, missing: str) -> None:
    """One plan, one decision — never an applied half and a refused half. Without the
    validation step the first case reaches the adapter and 500s, which both fails
    silently and degrades badly at once."""
    result = apply_plan(SEED_1, [op, bold(1)])
    assert result.html is None
    assert result.report.ok is False
    assert missing in result.report.errors[0]
    assert result.warnings == []


def test_verification_blocks_an_edit_the_engine_got_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate is about the ARTEFACT, not the code path: it re-parses the HTML string
    rather than trusting the applier's in-memory belief. If somebody breaks the renumber
    live, the app refuses the edit and says so instead of corrupting a patent."""
    real_render = apply_module.render

    def broken(doc):
        return real_render(doc).replace("<p>3. ", "<p>4. ")

    monkeypatch.setattr(apply_module, "render", broken)
    result = apply_plan(SEED_1, [bold(1)])

    assert result.html is None
    assert result.report.ok is False
    assert result.report.errors[0].endswith("The document was not changed.")
    assert SEED_1 == SEED_DOCUMENTS[0].content  # the input string is untouched


def test_a_heading_is_synthesised_only_when_the_result_would_stop_self_describing() -> None:
    """The >=2 fallback is not symmetric under deletion: a heading-less two-claim document
    that loses a claim re-parses as ZERO claims, so the verifier refused the edit and a
    user with two claims could never delete one at all. Adding markup nobody asked for is
    not free, so the guard fires only where it is needed."""
    result = apply_plan(TWO_CLAIMS_NO_HEADING, [delete(2)])
    assert result.html is not None, result.report.errors
    assert result.html.startswith("<h1>Claims</h1>")
    assert [c.blocks[0].html for c in parse(result.html).claims] == ["First claim text."]

    three = TWO_CLAIMS_NO_HEADING + "<p>3. Third claim text.</p>"
    assert "<h1>Claims</h1>" not in applied(three, [delete(3)])

    # And an empty plan changes nothing at all. The route decides `no_change` by
    # comparing the engine's output against the canonicalised input, so this pins the
    # equality that rests on.
    empty = apply_plan(SEED_1, [])
    assert empty.html == SEED_1
    assert empty.warnings == [] and empty.report.ok


def test_a_deleted_claim_referenced_from_the_specification_is_warned_about() -> None:
    """The renumber rewrites the WHOLE document, and the warning has to cover the whole
    document too.

    Both references below survive the substitution untouched, but they fail differently.
    "claim 3" inside a claim is a dangling reference the reader can see. "claim 3" in a
    Background paragraph is worse: after the renumber a DIFFERENT claim is claim 3, so
    the sentence points somewhere real and wrong. The scan used to walk `doc.claims`
    only, so the visible failure warned and the silent one did not.
    """
    html = (
        "<h1>BACKGROUND</h1><p>The embodiment of claim 3 is described in Figure 2.</p>"
        "<h1>Claims</h1><p>1. A device.</p><p>2. A method.</p>"
        "<p>3. The device of claim 1, being blue.</p><p>4. The device of claim 3, being red.</p>"
    )
    result = apply_plan(html, [delete(3)])
    assert result.html is not None

    assert W_DANGLING_REF_IN_TEXT.format(old=3) in result.warnings
    # The claim-side warning still names the referring claim by its NEW number: old 4
    # became 3 once the deleted claim was removed.
    assert W_DANGLING_REF.format(number=3, old=3) in result.warnings
    # Neither was rewritten — we never guess an author's intent in a legal document.
    assert result.html.count("of claim 3") == 2
    # ...and a reference that CAN be remapped still is, in the specification too.
    remapped = apply_plan(html, [delete(1)])
    assert remapped.html is not None
    assert "The embodiment of claim 2 is described" in remapped.html
    assert not [w for w in remapped.warnings if "specification still refers" in w]
