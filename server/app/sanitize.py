"""Sanitising for the save path: `sanitize_html` for content, `sanitize_text` for
the two plain-text fields (a patent's title and a version's name).

The allowlist is derived from what TipTap's StarterKit can actually render, not
from a generic "safe HTML" list. Stripping a tag StarterKit supports silently
destroys the user's content on save: a data-loss bug wearing a security costume.
"""

import html
import unicodedata

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


def sanitize_text(value: str) -> str:
    """Reduce a title or a version name to plain text.

    Defence in depth, not a live exploit: React escapes everything it renders and
    the client has no `dangerouslySetInnerHTML`. But `content` is the only field
    that passes a sanitiser today, so a name is the one string on this server that
    reaches a browser exactly as it was typed — and these names are also
    interpolated into 409 messages. Cleaning them here means no future consumer
    has to remember to.

    Control characters go because a name is a single line of text: a `\\r` in a
    title is invisible in the UI and corrupts anything that logs or exports it.
    Markup goes through the same nh3 pass as `content`, with an empty tag
    allowlist so nothing survives. nh3 then escapes what is left, which would
    store "R&D" as "R&amp;D", so the escaping is undone — the known cost is that
    a user who literally types "&lt;" gets "<" back, which is a better trade than
    mangling every ampersand.
    """
    unwrapped = "".join(
        " " if character in "\t\n\r" else character
        for character in value
        if character in "\t\n\r" or unicodedata.category(character)[0] != "C"
    )
    stripped = nh3.clean(
        unwrapped,
        tags=set(),
        clean_content_tags=set(STRIP_CONTENT_TAGS),
        strip_comments=True,
    )
    # Collapsed, not merely trimmed: a newline became a space above, and "Widget\r\n
    # for blood" must not read as "Widget  for blood" in the version dropdown.
    return " ".join(html.unescape(stripped).split())
