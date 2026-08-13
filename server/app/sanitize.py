"""HTML sanitising for the save path.

The allowlist is derived from what TipTap's StarterKit can actually render, not
from a generic "safe HTML" list. Stripping a tag StarterKit supports silently
destroys the user's content on save: a data-loss bug wearing a security costume.
"""

import nh3

ALLOWED_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "br",
        "hr",
        "strong",
        "em",
        "s",
    }
)
# StarterKit's ordered-list declares both `start` and `type`.
# Known cosmetic loss: code[class="language-*"] is stripped.
ALLOWED_ATTRIBUTES = {"ol": {"start", "type"}}
STRIP_CONTENT_TAGS = frozenset({"script", "style"})


def sanitize_html(html: str) -> str:
    # `tags=` replaces nh3's default tag list, but `attributes=` does not fully
    # replace its attribute handling: `title` and `lang` are permitted on every
    # allowed tag no matter what is passed here. Both are inert, so this is
    # documented rather than fought — see test_sanitize.py.
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        clean_content_tags=set(STRIP_CONTENT_TAGS),
        strip_comments=True,
    )
