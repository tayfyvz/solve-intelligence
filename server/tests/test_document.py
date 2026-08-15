"""The parser and the renderer — the round-trip contract every AI test rests on.

If these are red, a failing operation test means "the parser is wrong", not "the
operation is wrong", and the rest of the engine suite stops meaning anything.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.ai.document import BLOCK_TAGS, DROP_TAGS, INLINE_ONLY_TAGS, Block, parse, render
from app.data import SEED_DOCUMENTS
from app.sanitize import ALLOWED_TAGS, STRIP_CONTENT_TAGS, sanitize_html

SEED_1 = SEED_DOCUMENTS[0].content
SEED_2 = SEED_DOCUMENTS[1].content

AI_DIR = Path(__file__).parents[1] / "app" / "ai"
ENGINE_MODULES = sorted({p.stem for p in AI_DIR.glob("*.py")} - {"__init__", "llm", "graph"})


def f(html: str) -> str:
    return render(parse(html))


@pytest.mark.parametrize(
    ("seed", "blocks_per_claim"),
    [(SEED_1, [5, 1, 1, 4, 1, 5, 1, 1]), (SEED_2, [6, 1, 1, 1, 1, 1, 5, 1, 1])],
)
def test_a_seed_round_trips_byte_identically_and_parses_into_regions(
    seed: str, blocks_per_claim: list[int]
) -> None:
    """Everything rests on this one. `render(parse(x)) == x` for canonical input, and the
    three-region model reads the claims the user can see."""
    assert render(parse(seed)) == seed

    doc = parse(seed)
    assert [len(c.blocks) for c in doc.claims] == blocks_per_claim
    assert [c.number for c in doc.claims] == list(range(1, len(blocks_per_claim) + 1))
    assert [c.uid for c in doc.claims] == list(range(1, len(blocks_per_claim) + 1))
    assert {c.separator for c in doc.claims} == {"."}
    assert doc.claims_heading == Block("h1", "Claims")
    assert doc.preamble == [] and doc.postamble == []


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
def test_render_parse_is_idempotent_and_unwraps_containers(html: str) -> None:
    """`f(y) == f(f(y))` is the universal property the verifier rests on.

    Containers are UNWRAPPED, not coerced: coercing `<div><p>1. a</p><p>2. b</p></div>`
    to a `<p>` nests block elements inside a `<p>`, which html.parser re-reads as three
    siblings on the next parse — so idempotence was false for the commonest shape pasted
    in from Word.
    """
    out = f(html)
    assert f(out) == out
    assert "<div" not in out and "<p><p" not in out
    # The unwrapped paragraphs are real claims, not one merged block.
    unwrapped = parse(f("<h1>Claims</h1><div><p>1. a</p><p>2. b</p></div>"))
    assert [c.number for c in unwrapped.claims] == [1, 2]

    # And the depth bound is a bound, not a guess: `<div><div><div>…` must not blow the
    # stack, and the text inside must survive being coerced into one paragraph.
    deep = f("<div>" * 15 + "x" + "</div>" * 15)
    assert isinstance(deep, str) and "x" in deep


def test_escaping_and_non_ascii_survive_the_round_trip() -> None:
    """The formatter's `entity_substitution` argument, guarded from both sides.

    Dropping it disables escaping, so escaped text becomes live markup. Setting it to
    `substitute_html` instead names every non-ASCII character (`café` → `caf&eacute;`)
    while TipTap emits it raw, so identity would be false for any patent with an em-dash
    or an accent. Both seeds are pure ASCII, so nothing else would catch either.
    """
    escaped = '<p>a &amp; b &lt;x&gt; "q" it\'s</p>'
    assert f(escaped) == escaped
    assert "<script" not in f("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")

    unicode_html = "<p>café — 3° … μm ±5 Ω</p>"
    assert f(unicode_html) == unicode_html


@pytest.mark.parametrize("module", ENGINE_MODULES)
def test_engine_modules_never_import_openai_or_langgraph(module: str) -> None:
    """The engine is pure functions over parsed documents, which is what makes almost the
    whole suite runnable with no API key and no network.

    The module list is derived from the directory, so a new file under `app/ai/` is
    covered the day it is added. A fresh interpreter, because pytest has imported plenty.
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
    "html", ["", "   ", "<p></p>", "not html", "<p>1. a<p>2. b", "<h1>Claims</h1>"]
)
def test_degenerate_input_never_raises(html: str) -> None:
    assert isinstance(render(parse(html)), str)


def test_the_claims_region_is_found_conservatively() -> None:
    """A claim prefix only means a claim number inside the claims region. Everywhere else
    the digits are the author's text, and putting them back is what stops parse silently
    destroying content it merely failed to recognise."""
    # Heading variants, including the "short heading containing 'claim'" arm that exists
    # so a document-wide replace_text cannot make the region undetectable.
    for heading in ("<h1>CLAIMS</h1>", "<h2>What is claimed is:</h2>", "<h2>Patent Claims</h2>"):
        assert [c.number for c in parse(heading + "<p>1. a</p><p>2. b</p>").claims] == [1, 2]
    # …but prose that merely mentions claims is not a region marker.
    prose_heading = parse("<h2>Comparison with the claims of US 1,234,567 and family</h2>")
    assert prose_heading.claims_heading is None

    # No heading: two or more prefixed paragraphs are a claim set, one is not.
    assert [c.number for c in parse("<p>1. a</p><p>2. b</p>").claims] == [1, 2]
    assert parse("<p>1. only one claim</p>").claims == []
    assert f("<p>1. only one claim</p>") == "<p>1. only one claim</p>"

    # A numbered line in a Background keeps its digits and stays in the preamble.
    background = (
        "<h1>Background</h1><p>1. Field of the Invention</p><h1>Claims</h1><p>1. a</p><p>2. b</p>"
    )
    assert f(background) == background
    assert [b.html for b in parse(background).preamble] == [
        "Background",
        "1. Field of the Invention",
    ]

    # A leading orphan under the heading goes to the preamble, or "make claim 1 bold"
    # would bold "What is claimed is:" too.
    orphaned = parse("<h1>Claims</h1><p>What is claimed is:</p><p>1. a</p><p>2. b</p>")
    assert [b.html for b in orphaned.preamble] == ["What is claimed is:"]
    assert len(orphaned.claims[0].blocks) == 1

    # A year and a measurement are not claim numbers.
    prose = "<p>2024. In prior art</p><p>3.5 mm</p>"
    assert f(prose) == prose


@pytest.mark.parametrize(
    ("html", "marks", "inner"),
    [
        ("<p><strong>1. A x</strong></p>", ("strong",), "A x"),
        ("<p>1. <strong>A x</strong></p>", ("strong",), "A x"),
        ("<p><strong>1.</strong> A x</p>", (), "A x"),
        ("<p><em><strong>1. A x</strong></em></p>", ("em", "strong"), "A x"),
    ],
)
def test_a_claim_prefix_is_stripped_across_inline_markup(
    html: str, marks: tuple[str, ...], inner: str
) -> None:
    """Our own renderer emits `<p><strong>1. A…</strong></p>`, so the leading text node
    lives INSIDE the mark. A naive "consume across leading text nodes" misses the common
    case entirely and the claim is silently lost."""
    doc = parse("<h1>Claims</h1>" + html + "<p>2. b</p>")
    block = doc.claims[0].blocks[0]
    assert block.marks == marks
    assert block.html == inner
    assert "<strong></strong>" not in render(doc)


def test_the_engine_and_the_sanitiser_agree_about_which_tags_exist() -> None:
    """Set equations, not samples: a tag added to one path without a decision about the
    other fails here the same day.

    The correspondence is load-bearing in both directions. The sanitiser allows hr,
    blockquote, pre and h4-h6, so an engine that coerced them to `<p>` would let an AI
    edit destroy content Save had preserved — and script/style must be dropped WITH their
    text, or `<script>alert(1)</script>` becomes a visible paragraph reading "alert(1)"
    on the AI path while Save deletes it.
    """
    assert INLINE_ONLY_TAGS <= set(ALLOWED_TAGS)
    assert BLOCK_TAGS == set(ALLOWED_TAGS) - INLINE_ONLY_TAGS
    assert DROP_TAGS == set(STRIP_CONTENT_TAGS)

    block_shapes = (
        "<p>a</p><hr><ul><li>x</li><li>y</li></ul><blockquote><p>q</p></blockquote><h4>h</h4>"
    )
    assert f(block_shapes) == block_shapes
    assert f("<pre><code>def g():\n    return 1\n</code></pre>") == (
        "<pre><code>def g():\n    return 1\n</code></pre>"
    )

    dropped = (
        "<script>alert(1)</script><style>p{color:red}</style><h1>Claims</h1><p>1. A device.</p>"
    )
    assert f(dropped) == "<h1>Claims</h1><p>1. A device.</p>"
    assert f(dropped) == sanitize_html(f(dropped))
    nested = f("<div><p>1. a<script>alert(2)</script></p><p>2. b</p></div>")
    assert nested == "<p>1. a</p><p>2. b</p>"
