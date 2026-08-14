"""VF1-VF17 — the deterministic artefact gate.

Every fixture here is a string literal or a seed constant. No applier, no schemas, no
network, no database: this gate runs immediately after 3A, so that apply.py can be
written against a gate that already exists.

VF14 (citations through the production `Answer` shape) is a 4A gate row and VF18 (the
deterministic budget) is a 3C one — both need code that does not exist yet. The IDs are
not reused and not renumbered.
"""

import time

import pytest

from app.ai.document import parse
from app.ai.schemas import Answer, Citation
from app.ai.verify import (
    E_COUNT_MISMATCH,
    E_EMPTIED,
    E_NO_HEADING,
    E_NUMBERING,
    E_UNSTABLE,
    W_DANGLING_REF,
    W_HEADING_NO_CLAIMS,
    W_NO_CLAIMS,
    W_SELF_REF,
    VerifyReport,
    check_citations,
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
def test_unchanged_seed_verifies_clean(seed: str, count: int) -> None:
    """VF1 — and the standing proof that Patent 1's real claim-7 defect produces no
    warning: it points at claim 5, which exists. The seed's error is a SEMANTIC one that
    a deterministic checker cannot see, which is a good thing to be able to say out loud
    and a bad thing to use as the motivating case for the delta rules."""
    report = verify(seed, seed, expected_claims=count)
    assert report.ok
    assert report.errors == []
    assert report.warnings == []


@pytest.mark.parametrize("numbers", [[1, 2, 4], [1, 2, 2], [2, 3, 4], [1, 3, 2]])
def test_numbering_gap_is_an_error(numbers: list[int]) -> None:
    """VF2 — VF-E1 is deliberately not delta'd: the applier's renumber makes it
    unreachable on input nobody edited."""
    html = "<h1>Claims</h1>" + "".join(f"<p>{n}. claim text {i}</p>" for i, n in enumerate(numbers))
    report = verify(html, html, expected_claims=None)
    assert not report.ok
    assert report.errors == [
        E_NUMBERING.format(found=", ".join(str(n) for n in numbers), n=len(numbers))
    ]


def test_lost_claims_heading_is_an_error() -> None:
    """VF4."""
    before = "<h1>Claims</h1><p>1. a</p><p>2. b</p>"
    after = "<p>1. a</p><p>2. b</p>"
    report = verify(before, after, expected_claims=2)
    assert not report.ok
    assert E_NO_HEADING in report.errors

    # Gaining a heading is not an error.
    assert verify(after, before, expected_claims=2).ok


def test_expected_claim_count_mismatch_is_an_error() -> None:
    """VF5 — what catches a renderer that emits something the parser reads differently."""
    report = verify(SEED_1, SEED_1, expected_claims=7)
    assert not report.ok
    assert report.errors == [E_COUNT_MISMATCH.format(expected=7, found=8)]

    assert verify(SEED_1, SEED_1, expected_claims=None).ok


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("", "", 0),
        ("<p>prose only</p><p>and more prose</p>", "<p>prose only</p><p>and more prose</p>", 0),
        ("<h1>Claims</h1><p>1. a</p>", "<h1>Claims</h1><p>1. a</p>", 1),
        # No heading and one claim: the >=2 fallback reads it as no claims at all.
        ("<p>1. a</p>", "<p>1. a</p>", 0),
        # Hand-typed 1, 5 — the applier renumbers, and the renumber is not verify's business.
        ("<h1>Claims</h1><p>1. a</p><p>5. b</p>", "<h1>Claims</h1><p>1. a</p><p>2. b</p>", 2),
        (
            "<h1>Claims</h1><p>1. a</p><p>2. b</p><p>2. c</p><p>3. d</p>",
            "<h1>Claims</h1><p>1. a</p><p>2. b</p><p>3. c</p><p>4. d</p>",
            4,
        ),
        ("<p>&lt;script&gt;</p>", "<p>&lt;script&gt;</p>", 0),
        ("<hr>", "<hr>", 0),
    ],
)
def test_stress_shapes_do_not_error(before: str, after: str, expected: int) -> None:
    """VF6 — the eight rows of PLAN §19.6."""
    assert verify(before, after, expected_claims=expected).ok


def test_non_canonical_output_is_an_error() -> None:
    """VF7 — VF-E5 pins §15.5's idempotence invariant at runtime. `<p>1. <strong>x</strong>`
    renders back as `<p><strong>1. x</strong>`, so the input was never canonical."""
    report = verify("<p>a</p>", "<p>1. <strong>x</strong></p><p>2. y</p>", expected_claims=2)
    assert not report.ok
    assert E_UNSTABLE in report.errors


def test_emptying_the_document_is_an_error() -> None:
    """VF8."""
    report = verify(SEED_1, "", expected_claims=0)
    assert not report.ok
    assert E_EMPTIED in report.errors

    assert verify("", "", expected_claims=0).ok


def test_new_dangling_reference_is_a_warning_not_an_error() -> None:
    """VF9 — §18.6 rule 1 leaves the reference verbatim on purpose; we never guess. The
    honest response is to tell the user, not to refuse the edit."""
    before = (
        "<h1>Claims</h1><p>1. A device.</p><p>2. The device of claim 1.</p>"
        "<p>3. The device of claim 4.</p><p>4. The device of claim 1, being blue.</p>"
    )
    after = (
        "<h1>Claims</h1><p>1. A device.</p><p>2. The device of claim 1.</p>"
        "<p>3. The device of claim 4.</p>"
    )
    report = verify(before, after, expected_claims=3)
    assert report.ok
    assert report.warnings == [W_DANGLING_REF.format(number=3, target=4)]


def test_pre_existing_dangling_reference_is_not_reported() -> None:
    """VF10 — the VF-W1 delta. Without it, bolding claim 1 reports something true,
    irrelevant, and repeated on every request until the user learns to ignore the
    warnings array entirely, which is the day the array stops working."""
    before = "<h1>Claims</h1><p>1. A device.</p><p>2. The device of claim 99.</p>"
    after = "<h1>Claims</h1><p><strong>1. A device.</strong></p><p>2. The device of claim 99.</p>"
    assert verify(before, after, expected_claims=2).warnings == []


def test_self_reference_is_a_warning() -> None:
    """VF11."""
    before = FIVE_CLAIMS
    after = FIVE_CLAIMS.replace(
        "<p>4. The device of claim 2, wherein the glass is doped.</p>",
        "<p>4. The device of claim 4, wherein the glass is doped.</p>",
    )
    report = verify(before, after, expected_claims=5)
    assert report.ok
    assert report.warnings == [W_SELF_REF.format(number=4)]


def test_removing_every_claim_warns_in_the_specified_order() -> None:
    """VF12 — a list equality, pinning §19.3's ordering rule rather than membership.
    Specific before general: which claims are affected, then the whole document."""
    report = verify(FIVE_CLAIMS, "<h1>Claims</h1>", expected_claims=0)
    assert report.ok
    assert report.warnings == [W_HEADING_NO_CLAIMS, W_NO_CLAIMS]


def test_verify_is_pure() -> None:
    """VF13 — including that VerifyReport is NOT frozen, so nobody re-adds frozen=True
    without seeing why: it would freeze only the rebinding, not the lists, while
    synthesising a __hash__ that raises the moment a report lands in a set."""
    before, after = SEED_1, SEED_1
    first = verify(before, after, expected_claims=8)
    assert (before, after) == (SEED_1, SEED_1)

    first.errors.append("scribble")
    first.warnings.append("scribble")
    second = verify(SEED_1, SEED_1, expected_claims=8)
    assert second == VerifyReport(ok=True, errors=[], warnings=[])
    assert verify(SEED_1, SEED_1, expected_claims=8) == second


@pytest.mark.parametrize(
    ("html", "count"),
    [(SEED_1, 8), (SEED_2, 9)],
)
def test_the_sanitiser_and_the_serialiser_agree_on_the_seeds(html: str, count: int) -> None:
    """VF16(a, b) — the nh3/bs4 disagreement, which would otherwise fail 100% of edits.

    `_apply_and_verify` runs `verify(html, sanitize_html(engine_output), ...)`, so VF-E5
    asserts round-trip stability on a string NH3 serialised, not one our FORMATTER did.
    Any disagreement about entity policy, void-element form or attribute order fires
    VF-E5 on every single edit, and no other test puts both serialisers in one
    expression. If this fails, the fix is in FORMATTER — never in VF-E5.
    """
    assert sanitize_html(html) == html  # nh3 is a fixed point on canonical engine output
    assert verify(html, sanitize_html(html), expected_claims=count).ok


@pytest.mark.parametrize(
    "html",
    [
        # hr / ul / blockquote / h4: exactly where a void-element or nesting
        # disagreement between the two serialisers would surface.
        "<p>a</p><hr><ul><li>x</li><li>y</li></ul><blockquote><p>q</p></blockquote><h4>h</h4>",
        "<p>a &amp; b &lt;x&gt; it's</p>",
    ],
)
def test_the_sanitiser_and_the_serialiser_agree_on_edge_shapes(html: str) -> None:
    """VF16(c, d)."""
    assert sanitize_html(html) == html
    assert verify(html, sanitize_html(html), expected_claims=None).ok


def test_self_reference_created_by_renumbering_is_not_warned() -> None:
    """VF17 — the VF-W2 delta, both rules and a control.

    Claims 1 and 2 are deleted; §18.6 rule 1 leaves old claim 4's "of claim 2" verbatim,
    and the renumber makes that claim itself claim 2. An after-only VF-W2 would tell the
    user "Claim 2 refers to itself" about a condition they did not create, on the most
    ordinary deletion in the suite.
    """
    # Old claims 3, 4 and 5 survive and are renumbered 1, 2, 3. References to the DELETED
    # claims 1 and 2 are left verbatim (§18.6 rule 1 — we never guess); the reference to
    # the surviving old claim 3 is remapped to its new number, 1.
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
    assert W_SELF_REF.format(number=2) in control.warnings


def test_a_hand_typed_self_reference_carried_along_is_not_warned() -> None:
    """VF17, rule (a) — the user typed it; the edit merely carried it."""
    before = FIVE_CLAIMS.replace(
        "<p>3. The device of claim 1, wherein the housing is tapered.</p>",
        "<p>3. The device of claim 3, wherein the housing is tapered.</p>",
    )
    after = before.replace(
        "<p>1. A device comprising a housing.</p>",
        "<p><strong>1. A device comprising a housing.</strong></p>",
    )
    assert verify(before, after, expected_claims=5).warnings == []


def test_an_emptied_claim_cannot_be_expressed_in_html() -> None:
    """VF3 and VF15 cannot be written, and this is why — recorded as a test so that a
    future parser change surfaces it rather than leaving VF-E2 quietly dead.

    VF-E2 fires when a claim's blocks are all blank. But a claim only EXISTS because
    CLAIM_PREFIX_RE matched, and that pattern ends in `(?=\\S)` — it requires a non-space
    character after the number. Any such character is also non-space to `block_text`, so
    a parsed claim's first block is never blank. Emptying a claim in the HTML therefore
    does not produce an empty claim; it produces one claim FEWER, which VF-E1 and VF-E4
    already catch.
    """
    shapes = [
        "<h1>Claims</h1><p>1. a</p><p>2. </p>",
        "<h1>Claims</h1><p>1. a</p><p>2. &nbsp;</p>",
        "<h1>Claims</h1><p>1. a</p><p>2. <strong></strong></p>",
    ]
    for html in shapes:
        doc = parse(html)
        assert all(any(b.html.strip() for b in c.blocks) for c in doc.claims)
        assert len(doc.claims) == 1  # the stub folded into claim 1, it did not become claim 2

    # And the shape a caller would call "claim 2 was emptied" is caught by VF-E4.
    report = verify("<h1>Claims</h1><p>1. a</p><p>2. b</p>", shapes[0], expected_claims=2)
    assert not report.ok
    assert E_COUNT_MISMATCH.format(expected=2, found=1) in report.errors


def test_check_citations_flags_a_quote_that_is_not_in_the_document() -> None:
    """A hallucinated quotation is the failure mode that most damages trust in a legal
    tool, and it costs one substring search to catch."""
    doc = parse(SEED_1)
    real = "the biocompatible materials are glass"
    assert check_citations(doc, [("claim", "2", real)]) == []
    assert check_citations(doc, [("claim", "2", "the housing is made of titanium")]) != []
    # prior_art quotes are never checked here: the uploaded text is not in the document.
    assert check_citations(doc, [("prior_art", "US 1", "anything at all")]) == []


def test_deterministic_budget_at_the_input_cap(capsys: pytest.CaptureFixture) -> None:
    """VF18 — a 3C gate row, because the path it times runs through `apply_plan`.

    PLAN §3.4 reserves 2.0 s for ALL deterministic work in a run, and the whole timeout
    chain — the per-call ceiling, the graph deadline, the request timeout, the client's
    budget — is derived from that reservation holding. The figure originally came from
    the 2.7 KB seeds, where the real cost is ~25 ms. The AI path accepts 200 000
    characters, 70x that, and the cross-reference remap is O(claims x refs). A budget
    measured 70x below the cap is not a budget.

    This is a budget test, not a benchmark: one run, a generous threshold, and if it ever
    fails the fix is already written down in §3.4 — lower the AI-path cap, or widen the
    deadline gap.
    """
    from app.ai.apply import apply_plan
    from app.ai.outline import build_outline
    from app.ai.schemas import Op
    from app.config import get_settings

    cap = get_settings().max_html_chars
    body = (
        "The wireless optogenetic device of claim 1, wherein the body is made of "
        "biocompatible materials and is transparent and lightweight, and wherein the "
        "light transducing materials are lanthanide-doped nanoparticles."
    )
    claims = []
    number = 1
    html = "<h1>Claims</h1>"
    while len(html) < cap:
        claims.append(f"<p>{number}. {body}</p>")
        html = "<h1>Claims</h1>" + "".join(claims)
        number += 1
    assert len(html) >= cap and number > 100  # real work for the remap to do

    plan = [
        Op(kind="format_claim", claim_number=2, mark="bold", enabled=True),
        Op(kind="delete_claim", claim_number=3),
        Op(kind="replace_text", find="lightweight", replace="light"),
    ]

    start = time.perf_counter()
    doc = parse(html)
    build_outline(doc)
    result = apply_plan(html, plan)
    verify(html, result.html or html, expected_claims=None)
    elapsed = time.perf_counter() - start

    with capsys.disabled():
        print(f"\nVF18: {len(html):,} chars, {len(doc.claims)} claims -> {elapsed:.3f}s")
    assert result.html is not None, result.report.errors
    assert elapsed < 2.0, f"the §3.4 deterministic budget is blown: {elapsed:.3f}s"


def test_check_citations_and_verified_refs() -> None:
    """VF14 — a 4A gate row living here, because its fixtures are 4A types.

    The unpack is the one `_verify` does, and the second half is what pins the retype
    (PLAN §19.5): the same call made with plain triples — no Pydantic anywhere — must
    return the identical result. That is the assertion that verify.py never needs
    schemas.py, and therefore that this module could ship at 3D.
    """
    doc = parse(SEED_1)
    real = "the biocompatible materials are glass"
    answer = Answer(
        text="Claim 2 narrows the housing material.",
        citations=[
            Citation(kind="claim", ref="2", quote=real),
            Citation(kind="claim", ref="3", quote="a quotation nobody wrote"),
            Citation(kind="prior_art", ref="uploaded file", quote="also not in the document"),
        ],
    )
    triples = [(c.kind, c.ref, c.quote) for c in answer.citations]

    warnings = check_citations(doc, triples)
    assert len(warnings) == 1
    assert "a quotation nobody wrote" in warnings[0]
    assert "claim 3" in warnings[0]
    assert verified_claim_refs(doc, triples) == [2]

    # The retype: no Pydantic in sight, identical result.
    plain = [
        ("claim", "2", real),
        ("claim", "3", "a quotation nobody wrote"),
        ("prior_art", "uploaded file", "also not in the document"),
    ]
    assert check_citations(doc, plain) == warnings
    assert verified_claim_refs(doc, plain) == [2]


def test_verified_claim_refs_are_server_computed_and_never_model_supplied() -> None:
    """These become AiChatResponse.citations. A claim number only survives if its quote
    checks out AND the claim exists."""
    doc = parse(SEED_1)
    citations = [
        ("claim", "2", "the biocompatible materials are glass"),  # real
        ("claim", "3", "invented out of thin air"),  # bad quote
        ("claim", "99", "the biocompatible materials are glass"),  # no such claim
        ("section", "Claims", "the biocompatible materials are glass"),  # not a claim
    ]
    assert verified_claim_refs(doc, citations) == [2]
