"""Three plain-text views of a parsed document, for three different callers.

`build_outline` is what the planner reads to UNDERSTAND a document; `build_context` and
`claims_excerpt` are what the generating nodes read to WRITE one. The split exists
because an outline truncated at 240 characters cannot answer "what does claim 4 depend
on?" — and, as the live pre-flight proved, cannot support rewriting a claim either: the
model correctly refused, asking to be shown the text first.

All three emit plain text and never HTML. The model must not start thinking in markup.

Imports `document.py` and nothing else in this package; nothing imports this except the
graph's nodes.
"""

from __future__ import annotations

from app.ai.document import HEADING_TAGS, Block, ParsedDocument, block_text

__all__ = ["block_text", "build_outline", "build_context", "claims_excerpt"]

OUTLINE_HEADER = "DOCUMENT OUTLINE (reference only — do not copy it back)"
CONTEXT_HEADER = "DOCUMENT CONTEXT (full text — reference only, do not copy it back)"
CONTEXT_TAIL = "\n… (context truncated — the document is longer than shown) …"

_OUTLINE_LIMITS = (240, 120, 60)
_OUTLINE_KEEP = 10  # claim lines kept at each end when tier 4 drops the middle


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _headings(blocks: list[Block]) -> list[str]:
    return [block_text(b) for b in blocks if b.tag in HEADING_TAGS]


def _outline(doc: ParsedDocument, limit: int, *, drop_middle: bool) -> str:
    before = _headings(doc.preamble)
    if doc.claims_heading is not None:
        before.append("Claims (heading)")
    after = _headings(doc.postamble)

    claim_lines = []
    for claim in doc.claims:
        line = f"  {claim.number}{claim.separator} " + _truncate(block_text(claim.blocks[0]), limit)
        if len(claim.blocks) > 1:
            line += f" [+{len(claim.blocks) - 1} more paragraphs]"
        claim_lines.append(line)

    if drop_middle and len(claim_lines) > 2 * _OUTLINE_KEEP:
        first_omitted = doc.claims[_OUTLINE_KEEP].number
        last_omitted = doc.claims[-_OUTLINE_KEEP - 1].number
        claim_lines = [
            *claim_lines[:_OUTLINE_KEEP],
            f"  … (claims {first_omitted}–{last_omitted} omitted) …",
            *claim_lines[-_OUTLINE_KEEP:],
        ]

    return "\n".join(
        [
            OUTLINE_HEADER,
            "Sections before the claims: " + (", ".join(before) if before else "(none)"),
            f"Claims: {len(doc.claims)}",
            *claim_lines,
            "Sections after the claims: " + (", ".join(after) if after else "(none)"),
        ]
    )


def build_outline(doc: ParsedDocument, *, max_chars: int = 8000) -> str:
    """A one-line-per-claim map of the document, for the planner.

    Four deterministic tiers, each evaluated only if the previous result is still too
    long — never a "shrink until it fits" loop, so the same document always produces the
    same string.
    """
    for limit in _OUTLINE_LIMITS:
        out = _outline(doc, limit, drop_middle=False)
        if len(out) <= max_chars:
            return out
    return _outline(doc, _OUTLINE_LIMITS[-1], drop_middle=True)


def _claim_lines(claim, *, first_limit: int | None, rest_limit: int | None) -> list[str]:
    # The bracket form [N], not "N.", so that context echoed back by the model can never
    # be mistaken for a claim prefix by CLAIM_PREFIX_RE. A small thing that closes a real
    # feedback loop.
    head = block_text(claim.blocks[0])
    lines = [f"[{claim.number}] " + (_truncate(head, first_limit) if first_limit else head)]
    for block in claim.blocks[1:]:
        body = block_text(block)
        lines.append("    " + (_truncate(body, rest_limit) if rest_limit else body))
    return lines


def _context(
    doc: ParsedDocument,
    *,
    omit_postamble: bool,
    omit_preamble: bool,
    first_limit: int | None,
    rest_limit: int | None,
) -> str:
    def region(title: str, blocks: list[Block], omit: bool) -> list[str]:
        if omit and blocks:
            body = [f"(omitted — {len(blocks)} paragraphs)"]
        else:
            body = [block_text(b) for b in blocks] or ["(none)"]
        return ["", f"--- {title} ---", *body]

    parts = [CONTEXT_HEADER]
    parts += region("SECTIONS BEFORE THE CLAIMS", doc.preamble, omit_preamble)
    if doc.claims_heading is not None:
        parts += ["", "--- CLAIMS HEADING ---", block_text(doc.claims_heading)]
    parts += ["", f"--- CLAIMS ({len(doc.claims)}) ---"]
    if doc.claims:
        for claim in doc.claims:
            parts += _claim_lines(claim, first_limit=first_limit, rest_limit=rest_limit)
    else:
        parts.append("(none)")
    parts += region("SECTIONS AFTER THE CLAIMS", doc.postamble, omit_postamble)
    return "\n".join(parts)


def build_context(doc: ParsedDocument, *, max_chars: int = 30_000) -> str:
    """The full text of the document, for the Q&A branch.

    Five tiers. Tier 5 is the guarantee: the result is never longer than `max_chars`,
    for any document, including a pathological one claim of a million characters.
    """
    tiers = (
        {"omit_postamble": False, "omit_preamble": False, "first_limit": None, "rest_limit": None},
        {"omit_postamble": True, "omit_preamble": False, "first_limit": None, "rest_limit": None},
        {"omit_postamble": True, "omit_preamble": True, "first_limit": None, "rest_limit": None},
        {"omit_postamble": True, "omit_preamble": True, "first_limit": None, "rest_limit": 200},
        {"omit_postamble": True, "omit_preamble": True, "first_limit": 600, "rest_limit": 200},
    )
    for tier in tiers:
        out = _context(doc, **tier)
        if len(out) <= max_chars:
            return out
    return out[: max(0, max_chars - len(CONTEXT_TAIL))] + CONTEXT_TAIL


def claims_excerpt(doc: ParsedDocument, numbers, *, max_chars: int = 30_000) -> str:
    """The full text of selected claims — the view every GENERATING node reads.

    Never truncated per claim: truncating the very text a node is about to rewrite is
    the defect the live pre-flight found. An empty or fully-unknown set returns "", and
    the caller omits the block rather than emitting an empty header.
    """
    wanted = set(numbers)
    claims = sorted((c for c in doc.claims if c.number in wanted), key=lambda c: (c.number, c.uid))
    if not claims:
        return ""
    lines = ["RELEVANT CLAIMS, IN FULL"]
    for claim in claims:
        lines += _claim_lines(claim, first_limit=None, rest_limit=None)
    out = "\n".join(lines)
    if len(out) <= max_chars:
        return out
    return out[: max(0, max_chars - len(CONTEXT_TAIL))] + CONTEXT_TAIL
