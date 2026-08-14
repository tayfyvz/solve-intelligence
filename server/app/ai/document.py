"""The document model, the parser and the renderer.

Two guarantees, and everything in the AI layer rests on them:

1. **Identity on canonical input** — `render(parse(x)) == x` for any `x` already in
   TipTap's `getHTML()` shape. Both seeds are stored in that shape.
2. **Idempotence in general** — with `f = render ∘ parse`, `f(y) == f(f(y))` for any
   `y`. This is what makes it safe to run an AI edit on the output of an AI edit;
   `verify.py` enforces it at runtime.

Imports neither `openai` nor `langgraph` (CLAUDE.md invariant 1, test T5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape as _escape  # NOT `import html` — `parse(html: str)` shadows it

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.dammit import EntitySubstitution
from bs4.formatter import HTMLFormatter

MARK_TAGS = {"bold": "strong", "italic": "em", "strike": "s"}
MARK_ALIASES = {
    "strong": "strong",
    "b": "strong",
    "em": "em",
    "i": "em",
    "s": "s",
    "del": "s",
    "strike": "s",
}
MARK_ORDER = ("strong", "em", "s")  # deterministic nesting order for format_claim

# BLOCK_TAGS is exactly `app.sanitize.ALLOWED_TAGS - INLINE_ONLY_TAGS`, and T9 asserts
# that as a set equation rather than sampling it. The correspondence is load-bearing:
# the sanitiser allows hr, blockquote, pre and h4–h6, so an engine that coerced them to
# <p> would let an AI edit destroy content that Save had preserved.
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "pre", "hr"}
INLINE_ONLY_TAGS = frozenset({"li", "br", "code", "strong", "em", "s"})
VOID_TAGS = {"hr"}  # rendered `<hr>`, never `<hr></hr>`; html is always ""
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
NO_COLLAPSE_TAGS = {"pre"}  # whitespace is significant inside a code block
INLINE_DESCEND_TAGS = {"strong", "b", "em", "i", "s", "del", "strike", "code", "span"}

# `li` is deliberately not a block tag: parse walks top-level children only, so a
# <ul>/<ol> is ONE block whose html holds its <li>s verbatim. `code` and `br` likewise.
_INLINE_TAGS = INLINE_ONLY_TAGS | INLINE_DESCEND_TAGS

# The single most dangerous line in the file. `HTMLFormatter()`'s constructor defaults
# entity_substitution to None, which DISABLES escaping: `<p>a &amp; b &lt;script&gt;</p>`
# would serialise as `<p>a & b <script></p>` — escaped text becoming live markup. T4
# fails loudly if this argument is ever dropped.
#
# substitute_xml, NOT substitute_html (which PLAN §15.1 specified). Both enable
# escaping, so both close C17. They differ on everything else: substitute_html also
# names every non-ASCII character, so `café — 3°` serialises as `caf&eacute; &mdash;
# &deg;` while TipTap emits the characters raw — and identity, render(parse(x)) == x,
# would be false for any patent containing an em-dash or an accent. Both seeds are
# pure ASCII, so no other test would have noticed. substitute_xml escapes exactly
# `&`, `<` and `>`, which is precisely what the browser's serialiser does.
FORMATTER = HTMLFormatter(
    entity_substitution=EntitySubstitution.substitute_xml,
    void_element_close_prefix="",  # `<br>`, not bs4's default `<br/>`
)

CLAIM_PREFIX_RE = re.compile(r"^(\d{1,3})([.)])\s+(?=\S)")

# A cross-reference inside claim text: "claim 4", "Claims 12". Defined here rather than
# in apply.py because it describes a property of claim TEXT, not a step of the applier —
# and because both apply.py (the renumber remap) and verify.py need it. In apply.py it
# would make verify.py import apply.py, which imports verify.py.
REF_RE = re.compile(r"\b(claims?)(\s+)(\d{1,3})\b", re.IGNORECASE)

CLAIMS_HEADING_TEXTS = frozenset(
    {
        "claims",
        "claim",
        "the claims",
        "what is claimed is",
        "what is claimed",
        "i claim",
        "we claim",
    }
)
_CLAIM_WORD_RE = re.compile(r"\bclaims?\b")


@dataclass
class Block:
    tag: str  # lowercase, in BLOCK_TAGS; unknown elements are unwrapped or coerced to "p"
    html: str  # INNER html: whitespace collapsed (except in `pre`), claim prefix removed,
    #            whole-block marks peeled. Partial inline markup stays verbatim.
    marks: tuple[str, ...] = ()  # whole-block marks, OUTERMOST FIRST


@dataclass
class Claim:
    uid: int  # stable identity, assigned at parse, never reused, never renumbered.
    #           Operations address claims by uid; only outline and render use `number`.
    number: int  # as READ from the text — may be duplicated, out of order or skipped.
    separator: str  # "." or ")" — a `1) 2) 3)` document stays that way
    blocks: list[Block]  # blocks[0] is the numbered one


@dataclass
class ParsedDocument:
    preamble: list[Block] = field(default_factory=list)
    claims_heading: Block | None = None  # its own field, NOT an index (PLAN §1.2.1)
    claims: list[Claim] = field(default_factory=list)
    postamble: list[Block] = field(default_factory=list)


def block_text(block: Block) -> str:
    """Plain text of a block: tags removed, entities decoded, whitespace collapsed.

    Uses get_text(" ") so `<li>a</li><li>b</li>` reads "a b" rather than "ab". NOT
    interchangeable with the no-separator get_text() used for prefix detection.
    """
    return " ".join(BeautifulSoup(block.html, "html.parser").get_text(" ").split())


def all_blocks(doc: ParsedDocument) -> list[Block]:
    """Every block in the document, in render order. Defined once, here, because three
    modules walk the whole document and three private copies of this would drift."""
    blocks = list(doc.preamble)
    if doc.claims_heading is not None:
        blocks.append(doc.claims_heading)
    blocks += [b for c in doc.claims for b in c.blocks]
    return blocks + doc.postamble


def escape_text(text: str) -> str:
    """Escape LLM-authored PLAIN text for insertion into Block.html. Exactly once.

    quote=False is mandatory: the default turns `'` into `&#x27;` and TipTap emits the
    raw apostrophe, so "the system's" would flip a byte on the very next round trip.
    Text already in Block.html IS html and must never be re-escaped.
    """
    return _escape(text, quote=False)


# --------------------------------------------------------------------------- parse


def _is_container(node: object) -> bool:
    """A tag we do not model — <div>, <section>, <table> — that must be unwrapped."""
    return isinstance(node, Tag) and node.name not in BLOCK_TAGS and node.name not in _INLINE_TAGS


def _coerce_to_p(el: Tag, soup: BeautifulSoup) -> Tag:
    """The leaf case: an element holding only inline content becomes one <p>."""
    p = soup.new_tag("p")
    for child in list(el.contents):
        p.append(child.extract())
    return p


def _unwrap_container(el: Tag, out: list[Tag], soup: BeautifulSoup, depth: int) -> None:
    # Unwrapping, not coercion. Coercing <div><p>1. a</p><p>2. b</p></div> to a <p>
    # produced block elements nested inside a <p>, which html.parser re-reads as three
    # sibling paragraphs on the next parse — so f(y) == f(f(y)) was false for the single
    # most common shape pasted in from Word. Unwrapping converges in one pass.
    if depth > 10:
        # A bound, not a guess: a pathological <div><div><div>… must not recurse forever.
        out.append(_coerce_to_p(el, soup))
        return
    children = list(el.children)
    if any(isinstance(c, Tag) and (c.name in BLOCK_TAGS or _is_container(c)) for c in children):
        _collect(children, out, soup, depth + 1)
    else:
        out.append(_coerce_to_p(el, soup))


def _collect(nodes: list, out: list[Tag], soup: BeautifulSoup, depth: int) -> None:
    """Turn a run of siblings into top-level blocks, wrapping loose inline content."""
    loose: list = []

    def flush() -> None:
        if any(not (isinstance(n, NavigableString) and not str(n).strip()) for n in loose):
            p = soup.new_tag("p")
            for node in loose:
                p.append(node.extract())
            out.append(p)
        loose.clear()

    for node in nodes:
        if isinstance(node, Tag) and node.name in BLOCK_TAGS:
            flush()
            out.append(node)
        elif _is_container(node):
            flush()
            _unwrap_container(node, out, soup, depth)
        else:
            loose.append(node)
    flush()


def _top_level_blocks(html: str) -> list[Tag]:
    # html.parser, never lxml: different auto-closing behaviour, and it is not a dependency.
    soup = BeautifulSoup(html, "html.parser")
    out: list[Tag] = []
    _collect(list(soup.children), out, soup, 0)
    return out


def _text_nodes(el: Tag) -> list[NavigableString]:
    # Exact type, not isinstance: Comment and CData subclass NavigableString and are not text.
    return [n for n in el.descendants if type(n) is NavigableString]


def _collapse_whitespace(el: Tag) -> None:
    """Collapse runs of whitespace and trim the block's ends.

    This is what makes the pretty-printed seed and the collapsed getHTML() string
    converge on one shape.
    """
    if el.name in NO_COLLAPSE_TAGS:
        return
    for node in _text_nodes(el):
        node.replace_with(NavigableString(re.sub(r"\s+", " ", str(node))))
    nodes = _text_nodes(el)
    if not nodes:
        return
    nodes[0].replace_with(NavigableString(str(nodes[0]).lstrip()))
    nodes = _text_nodes(el)
    nodes[-1].replace_with(NavigableString(str(nodes[-1]).rstrip()))


def _drop_empty_leading_inlines(el: Tag) -> None:
    """Remove inline wrappers the prefix strip emptied.

    Without this `<p><strong>1.</strong> A device</p>` keeps an empty `<strong>`:
    _peel_marks then sees two children and refuses to peel, and the empty tag survives
    every round trip thereafter.
    """
    while True:
        children = [
            c for c in el.children if not (isinstance(c, NavigableString) and not str(c).strip())
        ]
        if not children or not isinstance(children[0], Tag):
            return
        first = children[0]
        if first.name not in INLINE_DESCEND_TAGS:
            return
        if first.get_text().strip() or first.find(["img", "br"]) is not None:
            return
        first.decompose()


def _strip_prefix(el: Tag) -> tuple[int, str] | None:
    """Detect and remove a leading claim prefix. Returns (number, separator), or None.

    Whitespace-insensitive and tolerant of a prefix split across nodes
    (`<strong>1.</strong> A device`): it matches the prefix's non-space characters in
    document order across leading text nodes, descending into inline elements.
    """
    # get_text(), NOT get_text(" "): the latter turns `<strong>1</strong>. A device` into
    # "1 . A device", which the regex rejects — a user who bolded only the digit would
    # silently lose the claim. block_text() uses the separator for the opposite reason.
    text = " ".join(el.get_text().split())
    m = CLAIM_PREFIX_RE.match(text)
    if m is None:
        return None
    want = [ch for ch in m.group(0) if not ch.isspace()]  # e.g. ['1', '2', '.']
    i = 0
    finished = False
    for node in list(el.descendants):
        if type(node) is not NavigableString:
            continue
        s = str(node)
        j = 0
        while j < len(s) and i < len(want):
            if s[j].isspace():
                j += 1
            elif s[j] == want[i]:
                i += 1
                j += 1
            else:
                return None  # defensive: the DOM disagrees with get_text()
        rest = s[j:]
        if i >= len(want):
            # Consume the prefix's trailing whitespace even when it lives in a later
            # text node — `<strong>1.</strong> A device` leaves nothing after the "."
            # in this node, so the strip is not finished until a later node yields text.
            rest = rest.lstrip()
            finished = bool(rest)
        node.replace_with(NavigableString(rest))
        if finished:
            break
    _drop_empty_leading_inlines(el)
    return int(m.group(1)), m.group(2)


def _peel_marks(el: Tag) -> tuple[str, ...]:
    """Strip whole-block mark wrappers into a canonical tuple, outermost first.

    A wrapper is 'whole-block' when the element's only non-whitespace content is a
    single mark element. `<b>` → "strong", `<i>` → "em", `<del>`/`<strike>` → "s".
    Parse preserves the ENCOUNTERED order so that render(parse(x)) == x holds even for
    an `<em><strong>…` document; only format_claim re-sorts into MARK_ORDER.
    """
    marks: list[str] = []
    while len(marks) < len(MARK_ORDER):
        children = [
            c for c in el.children if not (isinstance(c, NavigableString) and not str(c).strip())
        ]
        if len(children) != 1 or not isinstance(children[0], Tag):
            break
        canonical = MARK_ALIASES.get(children[0].name)
        if canonical is None or canonical in marks:
            break
        marks.append(canonical)
        children[0].unwrap()
    return tuple(marks)


def _is_claims_heading(block: Block) -> bool:
    """A heading introduces the claims region when its folded text is one of the known
    phrases, OR when it is a short heading containing the word "claim"/"claims".

    The containment arm exists because replace_text is document-wide: an instruction as
    innocent as "change Claims to Patent Claims" would otherwise make the heading
    undetectable on re-parse and a legitimate edit would be refused. Bounded at six
    words so that a body-style heading like "Comparison with the claims of US 1,234,567"
    — prose, not a region marker — cannot capture the region.
    """
    if block.tag not in HEADING_TAGS:
        return False
    folded = " ".join(re.sub(r"[^a-z0-9 ]+", "", block_text(block).casefold()).split())
    if folded in CLAIMS_HEADING_TEXTS:
        return True
    return len(folded.split()) <= 6 and _CLAIM_WORD_RE.search(folded) is not None


def _region_bounds(
    blocks: list[Block], prefixes: list[tuple[int, str] | None]
) -> tuple[int | None, int, int]:
    """Locate the claims region. Returns (heading index or None, start, end).

    start == end means there is no claims region and everything becomes preamble.
    """
    for i, block in enumerate(blocks):
        if _is_claims_heading(block):
            end = next(
                (j for j in range(i + 1, len(blocks)) if blocks[j].tag in HEADING_TAGS), len(blocks)
            )
            return i, i + 1, end

    # Fallback: no heading. C11 — "the first run of >=2 ADJACENT prefixed paragraphs" is
    # unimplementable, because Patent 1's claim 1 spans five paragraphs and only the
    # first carries a prefix, so the first adjacent run starts at claim 2. ">=2 hits
    # document-wide, terminated at the next heading, re-validated inside the region" is
    # the implementable form of the same intent.
    hits = [i for i, b in enumerate(blocks) if b.tag == "p" and prefixes[i] is not None]
    if len(hits) < 2:
        return None, 0, 0
    start = hits[0]
    end = next(
        (j for j in range(start + 1, len(blocks)) if blocks[j].tag in HEADING_TAGS), len(blocks)
    )
    if sum(1 for i in hits if start <= i < end) < 2:
        return None, 0, 0
    return None, start, end


def parse(html: str) -> ParsedDocument:
    """Parse body HTML into the three-region model. Never warns and never raises.

    Duplicate, out-of-order and skipped claim numbers are recorded verbatim: apply_plan
    is where duplicates warn, and renumber is where they are fixed.
    """
    blocks: list[Block] = []
    prefixes: list[tuple[int, str] | None] = []
    for el in _top_level_blocks(html):
        _collapse_whitespace(el)
        # Order matters: strip before peel, so both plausible renders of a bolded claim
        # (`<p><strong>1. A…</strong></p>` and `<p>1. <strong>A…</strong></p>`) converge
        # on marks=("strong",) with the same html.
        prefix = _strip_prefix(el) if el.name == "p" else None
        marks = _peel_marks(el)
        inner = "" if el.name in VOID_TAGS else el.decode_contents(formatter=FORMATTER)
        blocks.append(Block(el.name, inner, marks))
        prefixes.append(prefix)

    heading_idx, start, end = _region_bounds(blocks, prefixes)

    # A prefix is only a claim number inside the claims region. Everywhere else — a
    # Background reading "1. Field of the Invention", or the lone claim of a heading-less
    # one-claim document that the >=2 rule declines to treat as a claims region — the
    # digits are the author's text, and step 2 has already removed them. Put them back,
    # or parse silently destroys content it merely failed to recognise.
    for i, prefix in enumerate(prefixes):
        if prefix is not None and not (start <= i < end):
            blocks[i].html = f"{prefix[0]}{prefix[1]} {blocks[i].html}"

    doc = ParsedDocument()
    if heading_idx is None and start == end:
        doc.preamble = list(blocks)  # no claims region at all
        return doc
    if heading_idx is not None:
        doc.preamble = list(blocks[:heading_idx])
        doc.claims_heading = blocks[heading_idx]
    else:
        doc.preamble = list(blocks[:start])
    doc.postamble = list(blocks[end:])

    uid = 0
    for i in range(start, end):
        block, prefix = blocks[i], prefixes[i]
        if block.tag == "p" and prefix is not None:
            uid += 1
            doc.claims.append(Claim(uid=uid, number=prefix[0], separator=prefix[1], blocks=[block]))
        elif doc.claims:
            doc.claims[-1].blocks.append(block)
        else:
            # A leading orphan — `<p>What is claimed is:</p>` under the heading. It goes
            # to the preamble, not to claim 1, or "make claim 1 bold" would bold it too.
            doc.preamble.append(block)
    return doc


# -------------------------------------------------------------------------- render


def render_block(b: Block, prefix: str = "") -> str:
    if b.tag in VOID_TAGS:
        return f"<{b.tag}>"
    inner = prefix + b.html
    for tag in reversed(b.marks):  # marks are stored outermost-first
        inner = f"<{tag}>{inner}</{tag}>"
    return f"<{b.tag}>{inner}</{b.tag}>"


def render(doc: ParsedDocument) -> str:
    """Render the model back to body HTML.

    Marks wrap OUTSIDE the injected number → `<p><strong>1. A wireless…</strong></p>`,
    which is exactly what a human gets by selecting the line and pressing ⌘B. The number
    is still a field, and the parser sees through the tag via get_text().
    """
    out = [render_block(b) for b in doc.preamble]
    if doc.claims_heading is not None:
        out.append(render_block(doc.claims_heading))
    for c in doc.claims:
        for i, b in enumerate(c.blocks):
            out.append(render_block(b, prefix=f"{c.number}{c.separator} " if i == 0 else ""))
    out += [render_block(b) for b in doc.postamble]
    return "".join(out)  # no separator — TipTap emits `</p><p>` adjacent
