"""The deterministic gate between "the engine produced HTML" and "the user's document
changes".

Refusing to call setContent on a null `html` makes a FAILED request safe. It does nothing
about a successful but wrong one: a plan that applied cleanly, warned about nothing, and
left the claims numbered 1, 2, 2, 4. Every guarantee in document.py, operations.py and
apply.py is about a CODE PATH; this one is about the ARTEFACT, which is why it re-parses
the HTML string rather than trusting the applier's in-memory belief.

    ERROR   = "the engine broke a promise it makes."  Blocks the edit.
    WARNING = "the document has a property you should know about."  Applies the edit.

An error is by construction a bug in our code, so no user instruction should be able to
produce one — which is why every check that could be true of a document NOBODY edited is
computed against `before` as well, and only the difference is reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.ai.document import REF_RE, Claim, ParsedDocument, all_blocks, block_text, parse, render

MAX_ERRORS = 10
MAX_NUMBERS_IN_MESSAGE = 20
KEY_CHARS = 60
SNIP_CHARS = 60

E_NUMBERING = (
    "The edit left the claims numbered {found}, not 1 to {n}. The document was not changed."
)
E_EMPTY_CLAIM = "The edit left claim {number} empty. The document was not changed."
E_NO_HEADING = "The edit removed the Claims heading. The document was not changed."
E_COUNT_MISMATCH = (
    "The edited document did not read back correctly: {expected} claims were written "
    "but {found} were found. The document was not changed."
)
E_UNSTABLE = "The edited document was not stable when read back. The document was not changed."
E_EMPTIED = "The edit would have emptied the document. It was not applied."

W_DANGLING_REF = "Claim {number} refers to claim {target}, which does not exist."
W_SELF_REF = "Claim {number} refers to itself."
W_HEADING_NO_CLAIMS = "The document has a Claims heading but no claims."
W_NO_CLAIMS = "The edit removed every claim from the document."

W_UNVERIFIED_QUOTE = 'The AI quoted "{quote}" as {where}, but that text is not in this document.'

# The same catch, told honestly. A quote that is not in the document but IS one of this
# program's own placeholders is not an invented quotation — it is the model faithfully
# reading a marker we put in front of it because the document did not fit. Blaming it for
# a hallucination leaks an internal string and gives the user nothing to do.
W_QUOTED_SCAFFOLD = (
    "Part of that answer rests on a placeholder marking text I was not shown, not on the "
    "document itself. Ask about a specific section by name and I will read that part."
)

W_PARTIAL_CONTEXT = (
    "This document is too long for me to read in one go, so I did not see all of: {what}. "
    "Ask about one of those by name and I will read that part."
)

# Same fact, different advice: "ask about a section by name" is unfollowable with no
# headings, and a user who tries makes it WORSE, because the words in "the opening text
# (no heading)" match nothing and the retry scores lower than the question they asked
# first. Quoting a phrase always works against a lexical score.
W_PARTIAL_NO_HEADINGS = (
    "This document is too long for me to read in one go, and it has no section headings to "
    "aim at. Quote a phrase from the part you mean and I will find it."
)

# The most misleading case, and the one that was silent. When NOTHING in the question
# matched the document's wording, what the model was shown was chosen by POSITION rather
# than relevance. Saying only "I did not see all of X" is literally true and implies the
# opposite of the truth: that the parts it did see were the relevant ones.
W_NO_MATCH = (
    "None of the words in your question appear in this document, so I read it from the "
    "start rather than searching. Try the document's own wording, or quote a phrase from it."
)

# The EDITING branch's version of the same fact, and it needs its own words. "Ask about
# one of those by name and I will read that part" is advice for a question; here the user
# is being handed a change to accept or reject, and what they need to know is which part
# of their document the plan was made without reading.
W_PARTIAL_SPEC = (
    "This document is too long for me to read in one go, so I planned this change without "
    "seeing all of: {what}. Check those parts before you save."
)

MAX_LABELS_IN_MESSAGE = 5

# Every placeholder this program inserts into the model's context has this shape: a
# square-bracketed span between horizontal ellipses. `outline.OMITTED_MARK` is the only
# thing that produces one, and a test asserts the two agree — importing it would give this
# module a second dependency for the sake of one regex.
SCAFFOLD_RE = re.compile(r"\[\s*….*?…\s*\]", re.S)


# Deliberately not frozen: `frozen=True` on a class with two list fields freezes only the
# rebinding, not the lists, while synthesising a `__hash__` that raises the moment a
# report lands in a set. `verify` builds both lists locally and returns them without
# retaining a reference, which is where the immutability is real.
@dataclass
class VerifyReport:
    ok: bool
    errors: list[str]
    warnings: list[str]


def _key(claim: Claim) -> str:
    """Identity for delta purposes: the first 60 characters of the claim's first block,
    whitespace-collapsed and case-folded.

    Claims have no identity across a parse boundary, and their NUMBERS are exactly what
    an edit changes, so the text is the only stable handle available. 60 characters
    distinguishes every claim in either seed and is short enough that a small in-claim
    edit does not break the match.
    """
    if not claim.blocks:
        return ""
    return " ".join(block_text(claim.blocks[0]).casefold().split())[:KEY_CHARS]


def _refs(claim: Claim) -> list[int]:
    """Every claim number this claim's text points at, in document order."""
    return [int(m.group(3)) for block in claim.blocks for m in REF_RE.finditer(block_text(block))]


def _is_empty(claim: Claim) -> bool:
    return not claim.blocks or all(not block_text(b).strip() for b in claim.blocks)


def _dangling_pairs(doc: ParsedDocument) -> set[tuple[str, int]]:
    n = len(doc.claims)
    return {
        (_key(c), target) for c in doc.claims for target in _refs(c) if target < 1 or target > n
    }


def _self_pairs(doc: ParsedDocument) -> set[tuple[str, int]]:
    return {(_key(c), c.number) for c in doc.claims if c.number in _refs(c)}


def verify(
    before_html: str,
    after_html: str,
    *,
    expected_claims: int | None,
    deleted_numbers: frozenset[int] = frozenset(),
) -> VerifyReport:
    """Structurally validate an edited document. Pure: no LLM, no I/O, no mutation.

    `expected_claims` is the claim count the applier believes it produced. Checking the
    re-parsed output against it is what catches a renderer that emits something the parser
    reads differently. `None` skips that check, for callers verifying a document they did
    not produce.

    `deleted_numbers` is the set of ORIGINAL claim numbers the plan deleted, and exactly
    one check reads it: a self-reference whose target was deleted is suppressed, because
    the renumber manufactured it, not the user.
    """
    before = parse(before_html)
    after = parse(after_html)
    n = len(after.claims)

    errors: list[str] = []
    warnings: list[str] = []

    numbers = [c.number for c in after.claims]
    if numbers != list(range(1, n + 1)):
        found = ", ".join(str(x) for x in numbers[:MAX_NUMBERS_IN_MESSAGE])
        errors.append(E_NUMBERING.format(found=found, n=n))

    # Delta'd: a document that already contains an empty claim — a user pressed Enter
    # twice — would otherwise fail EVERY future request with an accusation nobody earned.
    if sum(_is_empty(c) for c in after.claims) > sum(_is_empty(c) for c in before.claims):
        first = next(c for c in after.claims if _is_empty(c))
        errors.append(E_EMPTY_CLAIM.format(number=first.number))

    if before.claims_heading is not None and after.claims_heading is None:
        errors.append(E_NO_HEADING)

    if expected_claims is not None and n != expected_claims:
        errors.append(E_COUNT_MISMATCH.format(expected=expected_claims, found=n))

    # The strongest check in the file: the idempotence invariant, evaluated on this
    # document at runtime. In one line it catches unescaped text in Block.html, a block
    # tag outside BLOCK_TAGS, an inserted claim whose text begins "10. ", and an inserted
    # section whose paragraphs re-parse as the claims region.
    if render(after) != after_html:
        errors.append(E_UNSTABLE)

    if before_html.strip() != "" and after_html.strip() == "":
        errors.append(E_EMPTIED)

    # Warning order is part of the contract: specific before general, so a reader sees
    # which claims are affected before being told something about the whole document.
    already_dangling = _dangling_pairs(before)
    dangling = sorted(
        {
            (c.number, target)
            for c in after.claims
            for target in _refs(c)
            if (target < 1 or target > n) and (_key(c), target) not in already_dangling
        }
    )
    warnings += [W_DANGLING_REF.format(number=num, target=tgt) for num, tgt in dangling]

    already_self = _self_pairs(before)
    warnings += [
        W_SELF_REF.format(number=c.number)
        for c in sorted(after.claims, key=lambda c: c.number)
        if c.number in _refs(c)
        and (_key(c), c.number) not in already_self
        # The renumber's doing, not the user's: claims 1-2 deleted, old claim 4 said "of
        # claim 2" and is now itself claim 2. The reference is left verbatim on purpose,
        # so accusing the user of it would be a false warning.
        and c.number not in deleted_numbers
    ]

    if after.claims_heading is not None and not after.claims:
        warnings.append(W_HEADING_NO_CLAIMS)
    if before.claims and not after.claims:
        warnings.append(W_NO_CLAIMS)

    errors = list(dict.fromkeys(errors))[:MAX_ERRORS]
    return VerifyReport(ok=not errors, errors=errors, warnings=warnings)


# ------------------------------------------------------------------ Q&A citations

Cite = tuple[str, str, str]  # (kind, ref, quote) — exactly the three strings used


def _snip(quote: str) -> str:
    collapsed = " ".join(quote.split())
    return collapsed if len(collapsed) <= SNIP_CHARS else collapsed[:SNIP_CHARS] + "…"


def _where(kind: str, ref: str) -> str:
    return f"claim {ref}" if kind == "claim" else f'the section "{ref}"'


def _plain_text(doc: ParsedDocument) -> str:
    joined = " ".join(block_text(b) for b in all_blocks(doc))
    return " ".join(joined.casefold().split())


def _quote_found(doc: ParsedDocument, quote: str) -> bool:
    needle = " ".join(quote.casefold().split())
    return bool(needle) and needle in _plain_text(doc)


def check_citations(doc: ParsedDocument, citations: Iterable[Cite]) -> list[str]:
    """Warnings for citations whose quote is not verbatim in the document.

    A quote 'checks out' when its whitespace-collapsed, case-folded form is a substring
    of the same treatment of the document's plain text. A hallucinated quotation is the
    failure mode that most damages trust in a legal tool, and it costs one substring
    search to catch.

    `prior_art` citations are not checked: the uploaded text is not part of the document
    and is not passed in. They are never rendered as chips either, so an unverifiable
    prior-art quote reaches the user only inside the prose, where the model already said
    where it came from.
    """
    out = [
        W_QUOTED_SCAFFOLD
        if SCAFFOLD_RE.search(quote)
        else W_UNVERIFIED_QUOTE.format(quote=_snip(quote), where=_where(kind, ref))
        for kind, ref, quote in citations
        if kind in ("claim", "section") and not _quote_found(doc, quote)
    ]
    # Deduplicated: W_QUOTED_SCAFFOLD carries no quote, so three citations that all
    # picked up a marker would otherwise print the same sentence three times.
    return list(dict.fromkeys(out))


def partial_context_warning(
    omitted: Iterable[str], *, matched: bool = True, headed: bool = True
) -> list[str]:
    """What the user is told when the model read only part of their document.

    Empty when the whole document was read, which — since the answer budget holds a
    37-page patent — is the common case. It has to stay empty there: a warning on every
    answer is a warning nobody reads.

    Three sentences rather than one, because the same omission means three different
    things and only one of them is "ask about that section":

    * `matched=False` — the question's words appear nowhere, so the selection was made by
      POSITION. This goes FIRST, because it changes what the rest means.
    * `headed=False`  — there are no section names to ask about.
    * otherwise       — name a section and it will be read.
    """
    what = _label_list(omitted)
    if what is None:
        return []
    which = [W_PARTIAL_NO_HEADINGS] if not headed else [W_PARTIAL_CONTEXT.format(what=what)]
    return ([] if matched else [W_NO_MATCH]) + which


def _label_list(omitted: Iterable[str]) -> str | None:
    """The quoted, capped, deduplicated label list both partial-view warnings render.
    None when there is nothing to name, which is the common case and must stay silent."""
    labels = list(dict.fromkeys(label for label in omitted if label))
    if not labels:
        return None
    shown = labels[:MAX_LABELS_IN_MESSAGE]
    what = ", ".join(f'"{label}"' for label in shown)
    if len(labels) > len(shown):
        what += f" and {len(labels) - len(shown)} more"
    return what


def partial_spec_warning(omitted: Iterable[str]) -> list[str]:
    """What the user is told when an EDIT was planned from part of the specification.

    Empty whenever the whole specification was read — which, since the spec budget is
    `max_html_chars`, is every document this app accepts. It exists for the same reason
    its Q&A sibling does: an edit built on a document the model only partly read must say
    so, and the editing branch had no channel to say it on at all.

    No `matched`/`headed` variants here. Those two exist because the answer branch's
    advice is "ask again, differently", and its usefulness depends on whether asking
    again can work. This warning gives no advice to invalidate — it names what was not
    read and leaves the decision with the user, which is right whatever the ranking did.
    """
    what = _label_list(omitted)
    return [] if what is None else [W_PARTIAL_SPEC.format(what=what)]


def verified_claim_refs(doc: ParsedDocument, citations: Iterable[Cite]) -> list[int]:
    """Claim numbers from citations whose quote checks out AND whose claim exists.
    Ascending, deduplicated. This is what becomes `AiChatResponse.citations`."""
    existing = {c.number for c in doc.claims}
    found = set()
    for kind, ref, quote in citations:
        if kind != "claim" or not re.fullmatch(r"\d{1,3}", ref.strip()):
            continue
        number = int(ref.strip())
        if number in existing and _quote_found(doc, quote):
            found.add(number)
    return sorted(found)
