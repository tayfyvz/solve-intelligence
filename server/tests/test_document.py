"""T1-T13 (parse/render half) — the round-trip contract every AI test rests on.

If these are red, a failing operation test means "the parser is wrong", not "the
operation is wrong", and the whole engine suite stops meaning anything.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.ai.document import (
    BLOCK_TAGS,
    DROP_TAGS,
    INLINE_ONLY_TAGS,
    Block,
    parse,
    render,
)
from app.data import SEED_DOCUMENTS
from app.sanitize import ALLOWED_TAGS, STRIP_CONTENT_TAGS, sanitize_html

SEED_1 = SEED_DOCUMENTS[0].content
SEED_2 = SEED_DOCUMENTS[1].content

AI_DIR = Path(__file__).parents[1] / "app" / "ai"
SEED_SOURCE = Path(__file__).parents[2] / "client" / "scripts" / "seed-source"


def f(html: str) -> str:
    return render(parse(html))


def pretty_body(name: str) -> str:
    """The body of a scaffold patent, still pretty-printed — the pre-normalisation shape."""
    raw = (SEED_SOURCE / name).read_text()
    return raw.split("<body>", 1)[1].split("</body>", 1)[0]


def test_seed_1_round_trips_byte_identically() -> None:
    """T1 — everything rests on this one."""
    assert render(parse(SEED_1)) == SEED_1


def test_seed_2_round_trips_byte_identically() -> None:
    """T2."""
    assert render(parse(SEED_2)) == SEED_2


@pytest.mark.parametrize(
    "html",
    [
        "<p>1. <strong>x</strong></p>",
        "<p>a<br/>b</p>",
        "<p>  spaced   out  </p>",
        "<div><p>1. a</p><p>2. b</p></div>",
        "<div>loose<p>a</p></div>",
        "<div><div><p>x</p></div></div>",
    ],
)
def test_render_parse_is_idempotent(html: str) -> None:
    """T3 — f(y) == f(f(y)), the universal property the verifier rests on."""
    assert f(html) == f(f(html))


@pytest.mark.parametrize(
    "html",
    [
        "<div><p>1. a</p><p>2. b</p></div>",
        "<div>loose<p>a</p></div>",
        "<div><div><p>x</p></div></div>",
    ],
)
def test_containers_are_unwrapped_not_coerced(html: str) -> None:
    """T3 — coercion produced <p><p>…</p></p>, which html.parser re-reads as siblings, so
    f(y) == f(f(y)) was false for the commonest shape pasted in from Word."""
    out = f(html)
    assert "<div" not in out
    assert "<p><p" not in out


def test_unwrapped_div_yields_two_claims() -> None:
    """T3 — the unwrapped paragraphs are real claims, not one merged block."""
    doc = parse("<h1>Claims</h1><div><p>1. a</p><p>2. b</p></div>")
    assert [c.number for c in doc.claims] == [1, 2]


def test_pretty_printed_seed_converges_on_the_normalised_form() -> None:
    """T3 — the trap named in CLAUDE.md: the scaffold's pretty-printed HTML and the
    stored getHTML() form are different strings that must parse to one shape."""
    assert f(pretty_body("patent-1.html")) == SEED_1


def test_entities_survive_the_round_trip() -> None:
    """T4 — guards C17. Dropping `entity_substitution` from FORMATTER disables escaping
    and turns escaped text into live markup. Do NOT delete this as redundant with T1:
    both seeds are entity-free."""
    html = '<p>a &amp; b &lt;x&gt; "q" it\'s</p>'
    out = f(html)
    assert out == html
    assert "<script" not in f("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")


def test_non_ascii_text_round_trips_unnamed() -> None:
    """T4 — the other half of the formatter choice, and the half no seed can catch.

    bs4's substitute_html names every non-ASCII character (`café` → `caf&eacute;`)
    while TipTap emits it raw, so identity would be false for any patent containing an
    em-dash, a degree sign, a Greek letter or an accent — which is most of them.
    """
    html = "<p>café — 3° … μm ±5 Ω</p>"
    assert f(html) == html


ENGINE_MODULES = sorted(
    {p.stem for p in AI_DIR.glob("*.py")} - {"__init__", "prompts", "llm", "graph"}
)


@pytest.mark.parametrize("module", ENGINE_MODULES)
def test_engine_modules_never_import_openai_or_langgraph(module: str) -> None:
    """T5 — invariant 1, enforced mechanically rather than in prose.

    The module list is DERIVED from the directory, so this test is correct at 3A when
    only two modules exist and still correct at 4C without anyone remembering to edit it.
    A fresh interpreter, because pytest has already imported plenty by now.
    """
    assert ENGINE_MODULES, "the glob found nothing — this test must not pass vacuously"
    code = (
        f"import sys, app.ai.{module}; print('openai' in sys.modules, 'langgraph' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False False", f"app.ai.{module} pulled one of them in"


@pytest.mark.parametrize(
    ("seed", "blocks_per_claim"),
    [(SEED_1, [5, 1, 1, 4, 1, 5, 1, 1]), (SEED_2, [6, 1, 1, 1, 1, 1, 5, 1, 1])],
)
def test_parse_structure(seed: str, blocks_per_claim: list[int]) -> None:
    """T6."""
    doc = parse(seed)
    assert [len(c.blocks) for c in doc.claims] == blocks_per_claim
    assert [c.number for c in doc.claims] == list(range(1, len(blocks_per_claim) + 1))
    assert {c.separator for c in doc.claims} == {"."}
    assert doc.claims_heading == Block("h1", "Claims")
    assert doc.preamble == []
    assert doc.postamble == []
    assert [c.uid for c in doc.claims] == list(range(1, len(blocks_per_claim) + 1))


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "<p></p>",
        "not html",
        "<p>1. only one claim</p>",
        "<div><p>1. a</p><p>2. b</p></div>",
        "<p>1. a<p>2. b",
        "<p>2024. In prior art</p><p>3.5 mm</p>",
        "<h1>Claims</h1>",
        "<p>&lt;script&gt;</p>",
    ],
)
def test_degenerate_inputs_never_raise(html: str) -> None:
    """T7."""
    out = render(parse(html))
    assert isinstance(out, str)
    if html == "":
        assert out == ""


def test_deep_nesting_does_not_recurse_without_limit() -> None:
    """T7 — the depth bound, so <div><div><div>… cannot blow the stack."""
    out = render(parse("<div>" * 15 + "x" + "</div>" * 15))
    assert isinstance(out, str)
    assert "x" in out


def test_the_claim_prefix_regex_rejects_prose() -> None:
    """T7 — a year and a measurement are not claim numbers, so their text must survive."""
    html = "<p>2024. In prior art</p><p>3.5 mm</p>"
    assert f(html) == html


def test_block_level_tags_survive_set_equation() -> None:
    """T9(a) — written against the imported constants, so a tag added to the sanitiser
    without a decision about the engine fails here the same day."""
    assert INLINE_ONLY_TAGS <= set(ALLOWED_TAGS)
    assert BLOCK_TAGS == set(ALLOWED_TAGS) - INLINE_ONLY_TAGS


def test_script_and_style_are_dropped_with_their_text() -> None:
    """T14 — the AI path and the save path must agree about the same bytes.

    An unmodelled tag is normally UNWRAPPED, text kept, which turned
    `<script>alert(1)</script>` into a visible `<p>alert(1)</p>`: inert, escaped text, but
    the AI path was promoting to prose exactly what Save deletes.
    """
    html = "<script>alert(1)</script><style>p{color:red}</style><h1>Claims</h1><p>1. A device.</p>"
    out = f(html)
    assert out == "<h1>Claims</h1><p>1. A device.</p>"
    assert out == sanitize_html(out)
    # Nested, and inside the claims region: decomposed before any block is collected.
    assert f("<div><p>1. a<script>alert(2)</script></p><p>2. b</p></div>") == (
        "<p>1. a</p><p>2. b</p>"
    )
    assert "alert" not in f(html) and "color:red" not in f(html)


def test_dropped_tags_are_exactly_the_sanitisers_stripped_tags() -> None:
    """T14(b) — a set equation, like T9(a): a tag added to one path without a decision
    about the other fails here the same day."""
    assert DROP_TAGS == set(STRIP_CONTENT_TAGS)


def test_block_level_tags_survive() -> None:
    """T9(b) — pairs with V11: nh3 allows these, so the engine must not destroy them."""
    html = "<p>a</p><hr><ul><li>x</li><li>y</li></ul><blockquote><p>q</p></blockquote><h4>h</h4>"
    assert f(html) == html


def test_pre_block_whitespace_is_preserved() -> None:
    """T11 — NO_COLLAPSE_TAGS."""
    html = "<pre><code>def f():\n    return 1\n</code></pre>"
    assert f(html) == html


@pytest.mark.parametrize(
    ("html", "marks", "inner"),
    [
        ("<p><strong>1. A x</strong></p>", ("strong",), "A x"),
        ("<p>1. <strong>A x</strong></p>", ("strong",), "A x"),
        ("<p><strong>1.</strong> A x</p>", (), "A x"),
        ("<p><em><strong>1. A x</strong></em></p>", ("em", "strong"), "A x"),
    ],
)
def test_strip_prefix_descends_and_handles_split_nodes(
    html: str, marks: tuple[str, ...], inner: str
) -> None:
    """T12 — C32. Our own renderer emits `<p><strong>1. A…</strong></p>`, so the leading
    text node lives INSIDE the mark; a naive "consume across leading text nodes" misses
    the common case entirely."""
    doc = parse("<h1>Claims</h1>" + html + "<p>2. b</p>")
    block = doc.claims[0].blocks[0]
    assert block.marks == marks
    assert block.html == inner
    assert "<strong></strong>" not in render(doc)


@pytest.mark.parametrize(
    "heading",
    [
        "<h1>CLAIMS</h1>",
        "<h2>What is claimed is:</h2>",
        "<h1>We Claim</h1>",
        "<h2>Patent Claims</h2>",
        "<h1>The Claims of the Invention</h1>",
    ],
)
def test_claims_heading_variants_are_detected(heading: str) -> None:
    """T13."""
    doc = parse(heading + "<p>1. a</p><p>2. b</p>")
    assert doc.claims_heading is not None
    assert [c.number for c in doc.claims] == [1, 2]


def test_a_prose_heading_mentioning_claims_is_not_a_region_marker() -> None:
    """T13 — seven words, so the containment arm must not fire."""
    doc = parse("<h2>Comparison with the claims of US 1,234,567 and its family</h2><p>text</p>")
    assert doc.claims_heading is None


def test_headingless_two_claim_document_uses_the_fallback() -> None:
    """T13 — the >=2-hits rule (C11)."""
    doc = parse("<p>1. a</p><p>2. b</p>")
    assert doc.claims_heading is None
    assert [c.number for c in doc.claims] == [1, 2]


def test_headingless_one_claim_document_has_no_claims() -> None:
    """T13 — conservative by design."""
    doc = parse("<p>1. only one claim</p>")
    assert doc.claims == []
    assert render(doc) == "<p>1. only one claim</p>"


def test_leading_orphan_lands_in_the_preamble() -> None:
    """T13 — otherwise "make claim 1 bold" bolds "What is claimed is:" too."""
    doc = parse("<h1>Claims</h1><p>What is claimed is:</p><p>1. a</p><p>2. b</p>")
    assert [b.html for b in doc.preamble] == ["What is claimed is:"]
    assert len(doc.claims[0].blocks) == 1


def test_a_claim_prefix_outside_the_claims_region_is_not_consumed() -> None:
    """Regression: step 2 strips a prefix from every <p> before the region is known, so
    a Background reading "1. Field of the Invention" lost its numbering entirely."""
    html = (
        "<h1>Background</h1><p>1. Field of the Invention</p><h1>Claims</h1><p>1. a</p><p>2. b</p>"
    )
    assert f(html) == html
    doc = parse(html)
    assert [b.html for b in doc.preamble] == ["Background", "1. Field of the Invention"]
    assert [c.number for c in doc.claims] == [1, 2]


def test_seed_1_claim_7_still_misreferences_claim_5() -> None:
    """Not a defect to fix — real seed data, and useful cross-reference test material."""
    doc = parse(SEED_1)
    assert re.search(r"\bclaim 5\b", doc.claims[6].blocks[0].html)
