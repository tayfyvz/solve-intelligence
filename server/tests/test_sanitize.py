"""V11 — the data-loss guard.

Stripping a tag StarterKit renders destroys the user's content on save, so the
"preserves" half of this test matters as much as the "strips" half.
"""

import pytest

from app.sanitize import sanitize_html

STRIPPED = [
    ("<script>alert(1)</script><p>a</p>", "<p>a</p>"),
    ("<style>p{color:red}</style><p>a</p>", "<p>a</p>"),
    ('<p onclick="steal()">a</p>', "<p>a</p>"),
    ('<p><a href="javascript:alert(1)">a</a></p>', "<p>a</p>"),
    ('<img src="x" onerror="alert(1)">', ""),
    ('<iframe src="evil"></iframe><p>a</p>', "<p>a</p>"),
    ('<p style="color:red">a</p>', "<p>a</p>"),
    ("<!-- comment --><p>a</p>", "<p>a</p>"),
]

# One behaviour, so one test: parametrising these inflates a single guarantee
# into a dozen reported cases. The seed patents are not repeated here — they are
# covered by test_seed.py and the client's seedRoundTrip.test.ts.
PRESERVED = [
    "",
    "<p><strong>1. A</strong></p>",
    "<h1>Claims</h1><h2>a</h2><h3>a</h3><h4>a</h4><h5>a</h5><h6>a</h6>",
    "<ul><li>a</li></ul>",
    '<ol start="3"><li>a</li></ol>',
    '<ol type="a"><li>a</li></ol>',
    "<blockquote><p>a</p></blockquote>",
    "<pre><code>a</code></pre>",
    "<p>a<br>b</p>",
    "<hr>",
    "<p><em>a</em><s>b</s></p>",
    "<p>a &amp; b &lt;c&gt;</p>",
]


@pytest.mark.parametrize(("html", "expected"), STRIPPED)
def test_dangerous_html_is_stripped(html: str, expected: str) -> None:
    assert sanitize_html(html) == expected


def test_editor_html_survives_unchanged() -> None:
    for html in PRESERVED:
        assert sanitize_html(html) == html


def test_title_and_lang_survive_despite_the_attribute_allowlist() -> None:
    """Pins nh3's actual behaviour, not the behaviour the allowlist implies.

    `attributes=` does not fully replace nh3's defaults: `title` and `lang` are
    permitted on every allowed tag regardless. Both are inert, so this documents
    the surprise rather than working around it — and fails loudly if a future nh3
    starts letting something less inert through.
    """
    assert sanitize_html('<p title="t" lang="en" id="x">a</p>') == '<p title="t" lang="en">a</p>'
