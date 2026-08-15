"""The save path's allowlist.

Stripping a tag TipTap's StarterKit renders destroys the user's content on save, so the
"preserves" half matters as much as the "strips" half: a data-loss bug wearing a security
costume.
"""

import pytest

from app.sanitize import sanitize_html, sanitize_text

STRIPPED = [
    ("<script>alert(1)</script><p>a</p>", "<p>a</p>"),
    ("<style>p{color:red}</style><p>a</p>", "<p>a</p>"),
    ('<p onclick="steal()">a</p>', "<p>a</p>"),
    ('<p><a href="javascript:alert(1)">a</a></p>', "<p>a</p>"),
    ('<img src="x" onerror="alert(1)">', ""),
    ('<iframe src="evil"></iframe><p>a</p>', "<p>a</p>"),
    ('<p style="color:red">a</p>', "<p>a</p>"),
    ("<!-- comment --><p>a</p>", "<p>a</p>"),
    # nh3's `attributes=` does not fully replace its defaults: `title` and `lang` are
    # permitted on every allowed tag regardless. Both are inert, so this documents the
    # surprise rather than working around it — and fails loudly if a future nh3 starts
    # letting something less inert through.
    ('<p title="t" lang="en" id="x">a</p>', '<p title="t" lang="en">a</p>'),
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
]


@pytest.mark.parametrize(("html", "expected"), STRIPPED)
def test_dangerous_html_is_stripped_and_editor_html_survives(html: str, expected: str) -> None:
    assert sanitize_html(html) == expected
    for safe in PRESERVED:
        assert sanitize_html(safe) == safe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<script>alert(1)</script>Widget", "Widget"),
        ("<b>Widget</b>", "Widget"),
        ('<img src="x" onerror="alert(1)">Widget', "Widget"),
        ("Widget\r\nfor\tblood", "Widget for blood"),  # a name is one line
        ("Widget\x00\x07", "Widget"),
        # The half that matters as much: ordinary punctuation in a real patent title.
        ("R&D widget", "R&D widget"),
        ("Method for cooling to T < 50 °C", "Method for cooling to T < 50 °C"),
        ("Wireless device — v2 (100 % duty)", "Wireless device — v2 (100 % duty)"),
    ],
)
def test_names_are_reduced_to_plain_text_without_mangling_punctuation(
    raw: str, expected: str
) -> None:
    assert sanitize_text(raw) == expected
