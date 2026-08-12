"""V11 — the data-loss guard.

Stripping a tag StarterKit renders destroys the user's content on save, so the
"preserves" half of this test matters as much as the "strips" half.
"""

import pytest

from app.data import SEED_DOCUMENTS
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
    *(seed.content for seed in SEED_DOCUMENTS),
]


@pytest.mark.parametrize(
    ("html", "expected"),
    [*STRIPPED, *((html, html) for html in PRESERVED)],
)
def test_sanitiser_round_trip(html: str, expected: str) -> None:
    assert sanitize_html(html) == expected
