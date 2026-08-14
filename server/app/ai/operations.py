"""The six operations, and the registry that dispatches to them.

One contract, shared by all six:

- they mutate `doc` in place, append human-readable strings to `warnings`, return None;
- **none of them raises.** The fail mode is always "no change, plus a warning the user
  can read". A legal document is not a place to guess;
- claims are addressed by **uid**, never by number. `uid is None` means the number the
  planner emitted does not exist in this document;
- **model-authored text enters `Block.html` through `escape_text(canonical_text(…))`,
  never through `escape_text` alone.** `parse()` collapses whitespace, so text carrying a
  trailing newline or a double space would render back into a document the next parse
  reads differently — and VF-E3 would throw the whole plan away for it;
- an operation never receives the `ApplyCtx`. Where one needs shared apply-time state,
  the adapter in `OPS` passes that *field* explicitly. Handing over the whole context
  would let any operation reach any field, which is exactly the coupling that makes
  "what can this function touch?" unanswerable in a pairing round.

`requested` is the claim number exactly as the planner emitted it, and it is used only
in warning text — never for lookup. That separation is what lets a warning name the
number the user typed while the code addresses the claim it resolved to.

Imports neither `openai` nor `langgraph`; `Op` is imported under TYPE_CHECKING only, so
there is no runtime dependency on `schemas.py` either (CLAUDE.md invariant 1, test T5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.ai.document import (
    MARK_ALIASES,
    MARK_ORDER,
    MARK_TAGS,
    NO_COLLAPSE_TAGS,
    Block,
    Claim,
    ParsedDocument,
    all_blocks,
    block_text,
    canonical_text,
    collapse_text,
    escape_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.ai.schemas import Op

MARK_WORDS = {"strong": "bold", "em": "italic", "s": "struck through"}

# The whole warning vocabulary, in one block, so that a live "what does the user
# actually see?" question has a one-screen answer.
W_NO_CLAIM = "There is no claim {requested} in this document."
W_ALREADY_DELETED = "Claim {requested} was already deleted."
W_ALREADY_MARKED = "Claim {number} is already {word}."
W_NOT_MARKED = "Claim {number} is not {word}, so nothing was removed."
W_PARTIAL_MARKS = "Claim {number} contains formatting inside its text that was left unchanged."
W_NO_ANCHOR = "There is no claim {requested} to insert after, so the new claim was skipped."
W_EMPTY_NEW_CLAIM = "A new claim was requested with no text, so it was skipped."
W_EMPTY_REPLACEMENT = "Claim {requested} was left unchanged because the replacement text was empty."
W_LOST_PARAGRAPHS = (
    "Claim {number} had {count} paragraphs; it was replaced with a single paragraph."
)
W_LOST_FORMATTING = "Formatting on claim {number} was dropped when it was replaced."
W_NO_HEADING = "The new section had no heading, so its paragraphs were inserted without one."
W_NO_PARAGRAPHS = 'The section "{heading}" was inserted with no paragraphs.'
W_EMPTY_FIND = "The text to find was empty, so nothing was replaced."
W_NOT_FOUND = '"{find}" was not found in the document, so nothing was replaced.'
W_SPLIT_BY_MARKUP = '"{find}" is split by formatting, so it was left unchanged.'
W_MULTIPLE_HITS = "That text appears {count} times; all of them were changed."


def next_uid(doc: ParsedDocument) -> int:
    return max((c.uid for c in doc.claims), default=0) + 1


def _find(doc: ParsedDocument, uid: int | None) -> Claim | None:
    return next((c for c in doc.claims if c.uid == uid), None) if uid is not None else None


def _has_inline_marks(block: Block) -> bool:
    return any("<" + tag in block.html for tag in MARK_ALIASES)


# ------------------------------------------------------------------ the six operations


def format_claim(
    doc: ParsedDocument,
    uid: int | None,
    mark: str,
    enabled: bool,
    warnings: list[str],
    *,
    requested: int,
) -> None:
    """Add or remove a whole-block mark on every block of a claim.

    Every block, because Patent 1's claim 1 is five paragraphs — bolding only the
    numbered one is not what anybody means. This is the behaviour the three-region model
    exists to make possible.
    """
    claim = _find(doc, uid)
    if claim is None:
        warnings.append(W_NO_CLAIM.format(requested=requested))
        return

    word = MARK_WORDS.get(mark, mark)
    present = [mark in b.marks for b in claim.blocks]
    if enabled and all(present):
        # "Already" means EVERY block carries it; a partially-marked claim is completed
        # silently, because that is the state the user was trying to reach.
        warnings.append(W_ALREADY_MARKED.format(number=claim.number, word=word))
        return
    if not enabled and not any(present):
        warnings.append(W_NOT_MARKED.format(number=claim.number, word=word))
        return

    for b in claim.blocks:
        marks = set(b.marks)
        if enabled:
            marks.add(mark)
        else:
            marks.discard(mark)
        # Canonical order, so bold-then-italic and italic-then-bold are the same bytes.
        b.marks = tuple(m for m in MARK_ORDER if m in marks)

    if any(_has_inline_marks(b) for b in claim.blocks):
        warnings.append(W_PARTIAL_MARKS.format(number=claim.number))


def delete_claim(
    doc: ParsedDocument,
    uid: int | None,
    warnings: list[str],
    *,
    requested: int,
    deleted_numbers: set[int],
) -> None:
    """Remove a claim. No renumbering here — that is apply step 5, exactly once."""
    if uid is None:
        warnings.append(W_NO_CLAIM.format(requested=requested))
        return
    claim = _find(doc, uid)
    if claim is None:
        warnings.append(W_ALREADY_DELETED.format(requested=requested))
        return
    # The ORIGINAL, as-parsed number, recorded BEFORE the renumber runs: afterwards it
    # exists nowhere. The dangling-reference pass and VF-W2's suppression both read it.
    deleted_numbers.add(claim.number)
    doc.claims.remove(claim)


def insert_claim(
    doc: ParsedDocument,
    after_uid: int | None,
    text: str,
    warnings: list[str],
    *,
    requested: int,
    at_start: bool,
    insert_cursor: dict[int, int],
) -> None:
    """Insert a new claim after an existing one, or at the start."""
    index = 0
    if not at_start:
        # The cursor is what makes [insert after 2 "A", insert after 2 "B"] produce
        # 2, A, B rather than 2, B, A.
        anchor = _find(doc, insert_cursor.get(after_uid, after_uid)) or _find(doc, after_uid)
        if anchor is None:
            # Never append at the end. Never guess a position in a legal document.
            warnings.append(W_NO_ANCHOR.format(requested=requested))
            return
        index = doc.claims.index(anchor) + 1

    if not text.strip():
        warnings.append(W_EMPTY_NEW_CLAIM)
        return

    uid = next_uid(doc)
    doc.claims.insert(
        index,
        Claim(
            uid=uid,
            number=0,  # a placeholder; renumber fixes it
            # So a `1) 2) 3)` document never gains a `4.` claim.
            separator=doc.claims[0].separator if doc.claims else ".",
            blocks=[Block("p", escape_text(canonical_text(text)))],
        ),
    )
    if not at_start and after_uid is not None:
        insert_cursor[after_uid] = uid


def replace_claim(
    doc: ParsedDocument,
    uid: int | None,
    text: str,
    warnings: list[str],
    *,
    requested: int,
) -> None:
    """Replace a claim's whole body with one paragraph of new text."""
    claim = _find(doc, uid)
    if claim is None:
        warnings.append(W_NO_CLAIM.format(requested=requested))
        return
    if not text.strip():
        warnings.append(W_EMPTY_REPLACEMENT.format(requested=requested))
        return  # never silently empty a claim

    if len(claim.blocks) > 1:
        warnings.append(W_LOST_PARAGRAPHS.format(number=claim.number, count=len(claim.blocks)))
    if any(b.marks for b in claim.blocks):
        warnings.append(W_LOST_FORMATTING.format(number=claim.number))
    claim.blocks = [Block("p", escape_text(canonical_text(text)))]


def insert_section(
    doc: ParsedDocument,
    heading: str,
    paragraphs: list[str],
    position: str,
    warnings: list[str],
) -> None:
    """Insert a headed prose section before or after the claims region."""
    tag = doc.claims_heading.tag if doc.claims_heading is not None else "h1"

    blocks: list[Block] = []
    if heading.strip():
        blocks.append(Block(tag, escape_text(canonical_text(heading))))
    else:
        warnings.append(W_NO_HEADING)

    bodies = [p for p in paragraphs if p.strip()]
    if not bodies:
        warnings.append(W_NO_PARAGRAPHS.format(heading=heading))
    blocks += [Block("p", escape_text(canonical_text(p))) for p in bodies]

    if not blocks:
        return

    # C9: without this, a Background reading "1. Field of the Invention" / "2. Description
    # of Related Art" satisfies the >=2 fallback on the NEXT parse and becomes the claims
    # region — the exact bug the three-region model exists to prevent.
    if doc.claims_heading is None:
        doc.claims_heading = Block("h1", "Claims")

    if position == "after_claims":
        doc.postamble[0:0] = blocks
    else:
        doc.preamble.extend(blocks)


def replace_text(doc: ParsedDocument, find: str, replace: str, warnings: list[str]) -> None:
    """Literal, case-sensitive, document-wide find and replace. No regex — the model
    does not get a regex engine.

    Claim numbers are structurally immune, because they are not in any Block.html. That
    is the payoff of "the number is a field", and O4 is the test.
    """
    if not find:
        warnings.append(W_EMPTY_FIND)
        return

    # Collapsed but NOT trimmed: a needle is matched inside a block, where a leading or
    # trailing space is part of what the user asked for — while a RUN of whitespace can
    # never appear in Block.html, so an uncollapsed needle simply never matches.
    find = collapse_text(find)
    needle, sub = escape_text(find), escape_text(collapse_text(replace))
    hits = 0
    split = False
    for block in all_blocks(doc):
        if needle in block.html:
            hits += block.html.count(needle)
            block.html = block.html.replace(needle, sub)
            if block.tag not in NO_COLLAPSE_TAGS:
                # The SPLICE can create whitespace the replacement itself does not carry:
                # "the widget of" with "gizmo " gives "the gizmo  of". Canonicalise the
                # spliced block, not just the substitution — `pre` excepted, because
                # parse() leaves its whitespace alone and so must this.
                block.html = canonical_text(block.html)
        elif find in block_text(block):
            # The honest degradation for a needle spanning tags. Cross-tag surgery is
            # impossible to defend live and impossible to test exhaustively.
            split = True

    if split:
        warnings.append(W_SPLIT_BY_MARKUP.format(find=find))
    elif hits == 0:
        warnings.append(W_NOT_FOUND.format(find=find))
    if hits > 1:
        # Document-wide replacement is the design, so say how much it touched rather
        # than let the user discover it later.
        warnings.append(W_MULTIPLE_HITS.format(count=hits))


# ------------------------------------------------------------------ uniform dispatch


@dataclass
class ApplyCtx:
    uid_by_number: dict[int, int]  # bound once, apply step 2
    insert_cursor: dict[int, int] = field(default_factory=dict)  # anchor uid -> last inserted
    deleted_numbers: set[int] = field(default_factory=set)  # ORIGINAL numbers, for the remap


OpFn = Callable[[ParsedDocument, "Op", ApplyCtx, list[str]], None]


def _do_format_claim(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    format_claim(
        doc,
        ctx.uid_by_number.get(op.claim_number),
        MARK_TAGS[op.mark],  # "bold" -> "strong", so the pure function sees canonical names
        op.enabled,
        warnings,
        requested=op.claim_number,
    )


def _do_delete_claim(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    delete_claim(
        doc,
        ctx.uid_by_number.get(op.claim_number),
        warnings,
        requested=op.claim_number,
        deleted_numbers=ctx.deleted_numbers,  # the field, never the ctx
    )


def _do_insert_claim(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    at_start = op.after_claim_number == 0  # 0 means "at the start"; it is not a uid
    insert_claim(
        doc,
        None if at_start else ctx.uid_by_number.get(op.after_claim_number),
        op.text,
        warnings,
        requested=op.after_claim_number,
        at_start=at_start,
        insert_cursor=ctx.insert_cursor,
    )


def _do_replace_claim(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    replace_claim(
        doc,
        ctx.uid_by_number.get(op.claim_number),
        op.text,
        warnings,
        requested=op.claim_number,
    )


def _do_insert_section(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    insert_section(doc, op.heading, op.paragraphs, op.position, warnings)


def _do_replace_text(doc: ParsedDocument, op: Op, ctx: ApplyCtx, warnings: list[str]) -> None:
    replace_text(doc, op.find, op.replace, warnings)


# Adding an operation is one function, one adapter, one dict entry and one prompt line —
# not edits in four places. OPS must be TOTAL over OpKind: apply_plan dispatches with
# OPS[op.kind] on a Pydantic-validated Literal, so a kind with no row here is a KeyError
# at request time, i.e. a 500 on a well-formed request. P9 asserts the equality.
OPS: dict[str, OpFn] = {
    "format_claim": _do_format_claim,
    "delete_claim": _do_delete_claim,
    "insert_claim": _do_insert_claim,
    "replace_claim": _do_replace_claim,
    "insert_section": _do_insert_section,
    "replace_text": _do_replace_text,
}
