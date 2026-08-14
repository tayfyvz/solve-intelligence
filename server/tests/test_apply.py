"""A1-A17 — the applier, end to end.

The four README examples are the acceptance tests. Every row calls `apply_plan` and
asserts on the `ApplyResult` the routes actually consume, so no test here exercises a
shape production never sees.
"""

import re

import pytest

from app.ai import apply as apply_module
from app.ai.apply import KIND_ORDER, W_DANGLING_REF, W_DUPLICATE_NUMBER, apply_plan
from app.ai.document import parse
from app.ai.operations import W_NO_ANCHOR, W_NO_CLAIM
from app.ai.schemas import Op
from app.data import SEED_DOCUMENTS

SEED_1 = SEED_DOCUMENTS[0].content

TWO_CLAIMS_NO_HEADING = "<p>1. First claim text.</p><p>2. Second claim text.</p>"
DUPLICATE_NUMBERS = "<h1>Claims</h1><p>1. a</p><p>2. b</p><p>2. c</p><p>2. d</p><p>3. e</p>"


def bold(n: int, enabled: bool = True) -> Op:
    return Op(kind="format_claim", claim_number=n, mark="bold", enabled=enabled)


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
    """A1 — README example 1."""
    result = apply_plan(SEED_1, [bold(1)])
    assert result.html is not None
    assert result.html.count("<strong>") == 5
    assert result.warnings == []
    assert result.report.ok
    for claim in parse(SEED_1).claims[1:]:
        assert claim.blocks[0].html in result.html


def test_example_2_delete_claim_3() -> None:
    """A2 — README example 2, with the cross-reference rewrites row by row."""
    out = applied(SEED_1, [delete(3)])
    doc = parse(out)
    assert len(doc.claims) == 7
    assert out.count("<p>") == 18
    assert [c.number for c in doc.claims] == list(range(1, 8))
    assert apply_plan(SEED_1, [delete(3)]).warnings == []

    original = parse(SEED_1).claims
    was_5, was_6, was_7, was_8 = original[4], original[5], original[6], original[7]
    assert "of claim 3" in doc.claims[3].blocks[0].html  # was 5, referred to claim 4
    assert "of claim 3" in doc.claims[4].blocks[2].html  # was 6
    # The seed's pre-existing error carried faithfully, not silently corrected: claim 7
    # says "claim 5" where it means 6, and 5 renumbers to 4.
    assert "of claim 4" in doc.claims[5].blocks[0].html  # was 7
    assert "of claim 5" in doc.claims[6].blocks[0].html  # was 8
    assert [was_5, was_6, was_7, was_8]  # the fixture is what this row claims it is


def test_delete_claim_1_produces_four_dangling_warnings() -> None:
    """A3 — the design's headline behaviour. The four README examples never reach this
    path (deleting claim 3 produces zero warnings), so it would otherwise ship untested.
    """
    result = apply_plan(SEED_1, [delete(1)])
    assert result.html is not None
    dangling = [w for w in result.warnings if "which was deleted" in w]
    assert len(dangling) == 4
    assert result.html.count("of claim 1") == 4  # left verbatim; we never guess


def test_delete_3_and_5_deletes_what_the_user_saw() -> None:
    """A4 — the whole reason binding happens before mutation.

    Naive: 1 2 3 4 5 6 → delete #3 → 1 2 4 5 6 → delete #5 → deletes old claim 6.
    """
    # A synthetic document with reference-free claim texts, so that what is compared is
    # WHICH claims survived and not how the remap rewrote them. "Claim number 3" is not a
    # cross-reference — REF_RE wants a digit straight after the word.
    html = "<h1>Claims</h1>" + "".join(
        f"<p>{n}. Claim number {n} text, distinct.</p>" for n in range(1, 7)
    )
    original = claim_texts(html)

    out = applied(html, [delete(3), delete(5)])
    assert claim_texts(out) == [original[i] for i in (0, 1, 3, 5)]
    assert applied(html, [delete(5), delete(3)]) == out  # order within a kind is irrelevant


def test_example_3_add_a_dependent_claim_after_claim_2() -> None:
    """A5 — README example 3. Note the seed's claim 2 already says glass; the prompt
    rules are what stop the model refusing it as redundant."""
    text = "The wireless optogenetic device of claim 2, wherein the glass is borosilicate."
    out = applied(SEED_1, [insert(2, text)])
    doc = parse(out)
    assert len(doc.claims) == 9
    assert out.count("<p>") == 20
    assert "<p>3. The wireless optogenetic device of claim 2," in out
    assert "of claim 5" in out and "of claim 4" not in out


def test_text_authored_by_this_plan_is_remapped() -> None:
    """A5 — C12. An insert_claim's text was written against the numbering the model was
    SHOWN, so inserting anywhere but at the end would point it at the wrong parent."""
    out = applied(SEED_1, [insert(0, "A device according to the method of claim 1.")])
    assert parse(out).claims[0].blocks[0].html.endswith("method of claim 2.")


def test_chained_inserts_on_one_anchor() -> None:
    """A6 — the insert cursor, which is what makes this 2, AAA, BBB and not 2, BBB, AAA."""
    out = applied(SEED_1, [insert(2, "AAA."), insert(2, "BBB.")])
    texts = claim_texts(out)
    assert texts[2] == "AAA."
    assert texts[3] == "BBB."


def test_example_4_write_a_background_section() -> None:
    """A7 — README example 4, and C9's regression: the inserted Background must not
    become the claims region on the next parse."""
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
    assert "<p>2. Description of Related Art</p>" in after
    assert len(parse(after).claims) == 7


def test_bold_then_reparse_then_delete_claim_3() -> None:
    """A8 — the peel → strip-prefix → renumber → remap interaction, which is the most
    intricate path in the design and the likeliest live question."""
    bolded = applied(SEED_1, [bold(1)])
    out = applied(bolded, [delete(3)])
    doc = parse(out)

    assert len(doc.claims) == 7
    assert [c.number for c in doc.claims] == list(range(1, 8))
    assert all("strong" in c.marks for c in doc.claims[0].blocks)
    assert len(doc.claims[0].blocks) == 5
    assert out.count("<strong>") == 5


def test_renumber_runs_exactly_once() -> None:
    """A9 — "exactly once" was prose-only, and a double renumber often still produces
    correct-looking output."""
    seen: list[list[int]] = []
    real_render = apply_module.render

    def spy(doc):
        seen.append([c.number for c in doc.claims])
        return real_render(doc)

    apply_module.render = spy
    try:
        out = applied(SEED_1, [delete(3), delete(5)])
    finally:
        apply_module.render = real_render

    assert seen == [[1, 2, 3, 4, 5, 6]]
    assert [c.number for c in parse(out).claims] == [1, 2, 3, 4, 5, 6]


def test_remap_does_not_cascade() -> None:
    """A10 — the direct regression for C10. A loop of str.replace over the mapping gives
    the chain 4→3, 5→4, 6→5, 7→6, 8→7 and cascades every reference down to 3."""
    html = "<h1>Claims</h1>" + "".join(
        f"<p>{n}. A widget as in claim {n + 1}.</p>" for n in range(1, 9)
    )
    out = applied(html, [delete(3)])
    targets = [int(m) for m in re.findall(r"claim (\d+)", out)]
    # Two of these are correct oddities, not remap failures: the second is the reference
    # to the DELETED claim 3, left verbatim because we never guess (§18.6 rule 1), and
    # the last is the reference to claim 9, which never existed and maps from nothing.
    assert targets == [2, 3, 4, 5, 6, 7, 9]
    assert targets != [3] * len(targets)  # what a loop of str.replace produces


def test_apply_on_a_document_with_no_claims() -> None:
    """A11 — §18.5a requires doc.claims to be non-empty, so no heading is synthesised."""
    result = apply_plan("<p>Just prose.</p>", [delete(1)])
    assert result.html == "<p>Just prose.</p>"
    assert result.warnings == [W_NO_CLAIM.format(requested=1)]
    assert result.report.ok
    assert "<h1>Claims</h1>" not in result.html


def test_empty_plan_is_a_no_op() -> None:
    """A12 — `_apply_and_verify` decides `no_change` by comparing the engine's output
    against the canonicalised input; this pins the equality that rests on."""
    result = apply_plan(SEED_1, [])
    assert result.html == SEED_1
    assert result.warnings == []
    assert result.report.ok


def test_apply_plan_blocks_a_bad_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """A13 — end-to-end proof of invariant 3 for the verification path. If somebody
    breaks the renumber live, in front of a reviewer, the app refuses the edit and says
    so rather than silently corrupting a patent."""
    real_render = apply_module.render

    def broken(doc):
        html = real_render(doc)
        return html.replace("<p>3. ", "<p>4. ")

    monkeypatch.setattr(apply_module, "render", broken)
    result = apply_plan(SEED_1, [bold(1)])

    assert result.html is None
    assert result.report.ok is False
    assert result.report.errors[0].endswith("The document was not changed.")
    assert isinstance(result.warnings, list)
    assert SEED_1 == SEED_DOCUMENTS[0].content  # the input string is untouched


def test_kind_order_adjacencies_are_load_bearing() -> None:
    """A14 — the plan calls two of these adjacencies "Necessary" and nothing tested them.

    Both plans below are written in the OPPOSITE order to KIND_ORDER, so it is the sort
    that decides, not the input order. Reversing KIND_ORDER makes each assertion fail.
    """
    # (a) replace_claim before format_claim: reversed, replace_claim rebuilds the blocks
    #     and discards the marks, and the bold is silently lost.
    out = applied(
        SEED_1,
        [bold(2), Op(kind="replace_claim", claim_number=2, text="A rewritten claim.")],
    )
    assert "<p><strong>2. A rewritten claim.</strong></p>" in out

    # (b) insert_claim before delete_claim: reversed, the anchor vanishes, the insert is
    #     skipped, and the user loses a claim with only a warning to show for it.
    result = apply_plan(SEED_1, [delete(3), insert(3, "A broader claim.")])
    assert result.html is not None
    assert len(parse(result.html).claims) == 8
    assert "A broader claim." in claim_texts(result.html)
    assert not [w for w in result.warnings if w == W_NO_ANCHOR.format(requested=3)]


def test_kind_order_is_total_over_the_operations() -> None:
    """A14 — a kind missing from KIND_ORDER is a ValueError inside a request handler."""
    from typing import get_args

    from app.ai.schemas import OpKind

    assert set(KIND_ORDER) == set(get_args(OpKind))


@pytest.mark.parametrize(
    ("op", "missing"),
    [
        (Op(kind="delete_claim"), "claim_number"),
        (Op(kind="format_claim", claim_number=1, mark="bold"), "enabled"),
        (Op(kind="insert_section", heading="H", paragraphs=[]), "position"),
    ],
)
def test_a_malformed_op_is_refused_not_raised(op: Op, missing: str) -> None:
    """A15 — without step 0 the first case reaches the adapter and 500s. CLAUDE.md's rule
    is degrade gracefully, never fail silently; a 500 does both wrong at once."""
    result = apply_plan(SEED_1, [op])
    assert result.html is None
    assert result.report.ok is False
    assert len(result.report.errors) == 1
    assert missing in result.report.errors[0]
    assert result.warnings == []


def test_a_malformed_op_refuses_the_whole_plan() -> None:
    """A15 — one plan, one decision. Never an applied half and a refused half."""
    result = apply_plan(SEED_1, [Op(kind="delete_claim"), bold(1)])
    assert result.html is None
    assert result.report.ok is False


def test_deleting_one_of_two_claims_without_a_heading_succeeds() -> None:
    """A16 — §18.5a. Before it, this returned html=None with VF-E4 "1 claim was written
    but 0 were found", so a user with a two-claim document could never delete a claim at
    all — a legitimate instruction the engine refused."""
    result = apply_plan(TWO_CLAIMS_NO_HEADING, [delete(2)])
    assert result.html is not None, result.report.errors
    assert result.report.ok
    assert result.html.startswith("<h1>Claims</h1>")

    doc = parse(result.html)
    assert len(doc.claims) == 1
    assert doc.claims[0].number == 1
    assert doc.claims[0].blocks[0].html == "First claim text."


def test_a_headingless_document_with_two_claims_left_gains_nothing() -> None:
    """A16 — the guard fires only when the result would no longer be self-describing.
    Adding markup the user did not ask for is not free."""
    html = TWO_CLAIMS_NO_HEADING + "<p>3. Third claim text.</p>"
    out = applied(html, [delete(3)])
    assert "<h1>Claims</h1>" not in out


def test_duplicate_claim_numbers_warn_once() -> None:
    """A17 — once per duplicated NUMBER, at the moment the table is built. Not once per
    occurrence, and not once per operation that resolves through it."""
    result = apply_plan(DUPLICATE_NUMBERS, [])
    assert result.html is not None
    assert result.warnings.count(W_DUPLICATE_NUMBER.format(number=2)) == 1
    assert [w for w in result.warnings if "appears more than once" in w] == [
        W_DUPLICATE_NUMBER.format(number=2)
    ]
    assert [c.number for c in parse(result.html).claims] == [1, 2, 3, 4, 5]
    assert result.report.ok


def test_dangling_reference_warning_names_the_new_number() -> None:
    """§18.6 rule 2 — the warning names the referring claim's NEW number, which is what
    the user sees when they read it."""
    html = (
        "<h1>Claims</h1><p>1. A device.</p><p>2. A method.</p>"
        "<p>3. The device of claim 2, being blue.</p>"
    )
    result = apply_plan(html, [delete(1), delete(2)])
    assert result.html is not None
    assert W_DANGLING_REF.format(number=1, old=2) in result.warnings
