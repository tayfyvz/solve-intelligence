"""Three plain-text views of a parsed document, for three different callers.

`build_outline` is what the planner reads to UNDERSTAND a document; `build_context` and
`claims_excerpt` are what the generating nodes read to WRITE one. The split exists
because an outline truncated at 240 characters cannot answer "what does claim 4 depend
on?" — and, as the live pre-flight proved, cannot support rewriting a claim either: the
model correctly refused, asking to be shown the text first.

`build_context` additionally decides **which parts of the document the question needs**.
On a 37-page patent the whole text does not fit in any sane budget, so the non-claim
paragraphs are ranked by lexical overlap with the question, packed to the budget, and
rendered in document order — and whatever did not fit is named, both inline and in a
manifest, so the user is never told an answer is complete when it is not.

All three emit plain text and never HTML. The model must not start thinking in markup.

Imports `document.py` and nothing else in this package; nothing imports this except the
graph's nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.document import HEADING_TAGS, Block, ParsedDocument, block_text

__all__ = [
    "STOPWORDS",
    "content_tokens",
    "ContextView",
    "Section",
    "block_text",
    "build_outline",
    "build_context",
    "claims_excerpt",
    "sections",
    "tokens",
]

OUTLINE_HEADER = "DOCUMENT OUTLINE (reference only — do not copy it back)"
CONTEXT_HEADER = "DOCUMENT CONTEXT (reference only, do not copy it back)"
CONTEXT_TAIL = "\n… (context truncated — the document is longer than shown) …"

_OUTLINE_LIMITS = (240, 120, 60)
_OUTLINE_KEEP = 10  # claim lines kept at each end when tier 4 drops the middle

# The label a section with no heading of its own gets. It has to read as a place a user
# could point at, because it is quoted back to them in the "not shown" warning.
UNTITLED_SECTION = "the opening text (no heading)"


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


# ------------------------------------------------------- words, for question scoping

_WORD_RE = re.compile(r"[a-z0-9]+")

# A short list of ENGLISH INFLECTIONS and nothing else. It folds "volumes"/"volume",
# "filters"/"filtering" and "oxygenating"/"oxygenate" together — the mismatch a user
# actually hits when their question and the patent describe the same thing in different
# grammatical forms.
#
# `-er`/`-ers`/`-ly`/`-est` are DELIBERATELY ABSENT. They were tried and removed: they
# merge "prime"/"primer" (a PCR primer is not priming) and "number"/"numb", while at the
# same time SPLITTING "filters"→"filt" from "filtering"→"filter". One suffix family caused
# both a false match and a miss, which is the worst of both, and in a legal document a
# false match is the more expensive half.
#
# It does NOT touch synonymy: "fill volume" against a document that says "priming volume"
# still scores zero, and no lexical scheme can fix that. That case is DETECTED instead
# (`ContextView.matched`) and the user is told to quote a phrase.
_SUFFIXES = ("ing", "ed")
_MIN_STEM = 4  # below this, stripping turns "gas" into "ga" and "the" into "th"
# The trailing `e` measures against a LOWER floor, which is what folds "gases" onto "gas"
# ("gases" -> "gase" -> "gas") and "bases" onto "bas". At 4 it would stop at "gase" and
# miss the commonest noun in this domain.
_MIN_Y_STEM = 3


def stem(word: str) -> str:
    """Fold one English inflection off a word, for scoring only.

    THE TRAILING `e` IS THE POINT, and it is why this is not a two-line suffix strip.
    Without it "volumes" folds to "volume" while "volume" stays "volume" — the two forms
    still miss each other and the whole exercise buys nothing. Stripping it from both lands
    them on "volum", and does the same for "oxygenating"/"oxygenate" and "gases"/"gas".

    A stem is a SORT KEY and nothing else. It never reaches the model, the document or a
    citation, so a wrong fold costs relevance and can never cost correctness.
    """
    for suffix, replacement in (("ies", "y"), ("ied", "y")):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_Y_STEM:
            word = word[: -len(suffix)] + replacement
            break
    else:
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
                word = word[: -len(suffix)]
                break
        else:
            # A plural, but never "less" -> "les": a double s is not an inflection.
            if word.endswith("s") and not word.endswith("ss") and len(word) - 1 >= _MIN_STEM:
                word = word[:-1]

    return word[:-1] if word.endswith("e") and len(word) - 1 >= _MIN_Y_STEM else word


def tokens(text: str) -> set[str]:
    """The scoring key for a piece of text: lowercase words, one inflection folded off.

    Both sides of every comparison go through this, so the folding only has to be
    CONSISTENT, not linguistically correct.
    """
    return {stem(word) for word in _WORD_RE.findall(text.lower())}


# ~40 common English words. Kept as a literal rather than pulled from a library: it is
# read once, in a lexical-overlap score, and a dependency for forty strings is a joke.
# It lives here rather than in `nodes.py` because `build_context` scores sections with
# it and `outline.py` may not import `nodes.py` — nodes imports outline. `nodes.py`
# re-exports both names, so every existing caller reads unchanged.
#
# RAW, and subtracted BEFORE stemming — see `content_tokens`.
STOPWORDS = frozenset(
    """a an and are as at be but by can could do does for from has have how i if in
    into is it its me my of on or please should so than that the their them then there
    these they this to was were what when where which who will with would you your""".split()
)


def content_tokens(text: str) -> set[str]:
    """The question's content words, as scoring keys.

    Stopwords are removed BEFORE stemming, and the order is the whole point. Stemming
    first and subtracting after deletes any content word whose STEM collides with a
    stopword's: "shoulder" folds to "should", so `tokens(q) - STOPWORDS` silently dropped
    it and a question about the shoulder of a housing was answered "none of the words in
    your question appear in this document" — with the word sitting right there. Same for
    "thane"/"than", "wither"/"with", "theirs"/"their".
    """
    return {stem(word) for word in _WORD_RE.findall(text.lower()) if word not in STOPWORDS}


# --------------------------------------------------------------------- the sections


@dataclass(frozen=True)
class Section:
    """A heading and the body blocks under it, from the NON-claim regions.

    The claims are not sections: they have their own model, their own numbering rules and
    their own view (`claims_excerpt`). This type describes the specification around them —
    Field, Background, Summary, Detailed Description, Abstract — which is where a
    question about "the Background" has to be answered from.
    """

    heading: str  # "" for a run that precedes the first heading
    blocks: tuple[Block, ...]  # body only; the heading block itself is not in here
    after_claims: bool

    @property
    def label(self) -> str:
        """How this section is named to the user. Never empty."""
        return self.heading or UNTITLED_SECTION


def sections(doc: ParsedDocument) -> list[Section]:
    """Split the preamble and postamble into heading-delimited sections, in document
    order, preamble first. A document with no headings at all yields one untitled
    section holding everything."""
    out: list[Section] = []
    for blocks, after in ((doc.preamble, False), (doc.postamble, True)):
        heading, body = "", []
        for block in blocks:
            if block.tag in HEADING_TAGS:
                if heading or body:
                    out.append(Section(heading, tuple(body), after))
                heading, body = block_text(block), []
            else:
                body.append(block)
        if heading or body:
            out.append(Section(heading, tuple(body), after))
    return out


Ref = tuple[int, int]  # (section index, paragraph index)

# A heading match is a TIEBREAK, not a multiplier. Uncapped, `len(words & heading_tokens)`
# scaled with the heading's length, so on "what is the priming volume in the detailed
# description of the preferred embodiments?" every filler paragraph under that 6-word
# heading scored 4 while the paragraph actually containing "priming volume … 220
# millilitres" scored 2 and never made the cut — naming a section actively destroyed
# retrieval whenever the fact was filed somewhere else, which in a patent is the norm.
_HEADING_BONUS_CAP = 1


def _rank(secs: list[Section], words: set[str]) -> tuple[list[Ref], bool]:
    """(section, paragraph) pairs most relevant first, and whether ANYTHING matched.

    A paragraph scores on its own overlap with the question plus a capped bonus if the
    question's words hit its section HEADING, so "what does the Background say about
    priming volume?" pulls the whole Background forward and not merely the paragraphs that
    happen to repeat the noun.

    When NOTHING scores — the question's words appear nowhere, which happens whenever the
    user's vocabulary differs from the document's ("priming volume" vs "fill volume") and
    on every "summarise this patent" — the fallback is round-robin ACROSS sections rather
    than document order. Document order answers "summarise this" from the front of the
    first section and omits the section literally called Summary; round-robin gives every
    section its opening paragraphs, which is what a summary needs. Both keys are total
    orders, so either way the same document and question give the same ranking.
    """
    scored: list[tuple[int, int, int]] = []
    matched = False
    for si, section in enumerate(secs):
        bonus = min(_HEADING_BONUS_CAP, len(words & tokens(section.heading)))
        for bi, block in enumerate(section.blocks):
            score = len(words & tokens(block_text(block))) + bonus
            matched = matched or score > 0
            scored.append((-score, si, bi))
    if not matched:
        # (paragraph, section): paragraph 0 of every section, then paragraph 1, and so on.
        return sorted(((si, bi) for _, si, bi in scored), key=lambda r: (r[1], r[0])), False
    scored.sort()
    return [(si, bi) for _, si, bi in scored], True


@dataclass(frozen=True)
class _Pack:
    """What survived the budget, and the one paragraph that was cut down rather than cut."""

    kept: frozenset[Ref] = frozenset()
    # The TOP-RANKED paragraph, when it alone was bigger than the whole budget:
    # (section, paragraph, characters kept). Never more than one — everything behind it
    # keeps the ordinary skip rule.
    clipped: tuple[int, int, int] | None = None


def _pack(secs: list[Section], order: list[Ref], budget: int) -> _Pack:
    """Take paragraphs in rank order while they fit.

    Two rules, and the second exists because the first alone breaks a promise:

    1. A paragraph too big for the REMAINING budget is SKIPPED rather than ending the
       walk, so one 9,000-character paragraph cannot shut out the twenty short ones
       behind it. Same greedy rule as `nodes.select_paragraphs`.
    2. **Except the top-ranked one.** A single Detailed Description paragraph larger than
       the whole budget is not exotic, and skipping it drops the only paragraph that
       mentions the question at all, fills the budget with prose that scored zero, and
       then tells the user to "ask about that section by name" — advice that provably
       cannot work, because asking again produces the same skip. It is clipped instead.
    """
    kept: set[Ref] = set()
    clipped: tuple[int, int, int] | None = None
    used = 0
    for rank, (si, bi) in enumerate(order):
        cost = len(block_text(secs[si].blocks[bi])) + 1  # + the newline it is joined with
        if used + cost > budget:
            if rank == 0 and budget > _MIN_CLIP_CHARS:
                clipped = (si, bi, budget - 1)
                used = budget
            continue
        kept.add((si, bi))
        used += cost
    return _Pack(frozenset(kept), clipped)


# Below this there is no room for a clip worth reading, so the ordinary skip stands.
_MIN_CLIP_CHARS = 200


# The inline elision marker. Bracketed, like the `[4]` claim prefix, so that it reads as
# this program's scaffolding rather than as the document's own words — and `verify.py`
# recognises the shape, so a model that quotes one gets the actionable sentence about a
# partly-read document instead of an accusation of inventing a quotation.
OMITTED_MARK = "[… {n} paragraph{s} not shown here …]"


CLIPPED_MARK = "[… the rest of this paragraph is not shown here …]"


def _elision(n: int) -> str:
    return OMITTED_MARK.format(n=n, s="" if n == 1 else "s")


@dataclass(frozen=True)
class ContextView:
    """What the answer node is shown, and what it was not shown.

    `omitted` is the whole point of the type. A string alone cannot tell the caller that
    a question about the Detailed Description was answered from the claims, and that is
    exactly the case the user has to be told about.
    """

    text: str
    omitted: tuple[str, ...] = ()  # labels of sections not shown IN FULL
    omitted_paragraphs: int = 0
    # False when NOTHING in the question matched the document's wording, so what was kept
    # was chosen by position, not by relevance. Without this the user is told "I did not
    # see all of X" — literally true and completely misleading, because it implies the
    # parts they WERE shown were the relevant ones.
    matched: bool = True
    # False when the document has no headings at all (a .txt import). "Ask about a section
    # by name" is unfollowable then, and a user who tries makes the retrieval WORSE.
    headed: bool = True


def _render_region(
    secs: list[Section], pack: _Pack, *, after_claims: bool
) -> tuple[list[str], list[str], int]:
    """(lines, labels not shown in full, paragraphs dropped) for one region."""
    lines: list[str] = []
    partial: list[str] = []
    dropped = 0
    for si, section in enumerate(secs):
        if section.after_claims != after_claims:
            continue
        body: list[str] = []
        run = 0  # consecutive dropped paragraphs, flushed as one marker
        clip = pack.clipped
        shown = {bi for (s_i, bi) in pack.kept if s_i == si}
        if clip is not None and clip[0] == si:
            shown.add(clip[1])
        for bi, block in enumerate(section.blocks):
            if bi in shown:
                if run:
                    body.append(_elision(run))
                    run = 0
                text = block_text(block)
                if clip is not None and (clip[0], clip[1]) == (si, bi):
                    # Cut down rather than cut out — see `_pack` rule 2.
                    text = text[: clip[2]].rstrip() + " " + CLIPPED_MARK
                body.append(text)
            else:
                run += 1
        if run:
            body.append(_elision(run))
        dropped += sum(1 for bi in range(len(section.blocks)) if bi not in shown)
        # A clipped paragraph is shown but NOT in full, so its section is still "partial".
        clipped_here = clip is not None and clip[0] == si
        if shown or not section.blocks:
            lines += ["", f"## {section.label}", *body]
        else:
            # Nothing of it survived: naming it here as well as in the manifest would
            # give the model a heading with no text under it, which reads as "this
            # section is empty" rather than "you have not been shown it".
            partial.append(section.label)
            continue
        if clipped_here or any(bi not in shown for bi in range(len(section.blocks))):
            partial.append(section.label)
    return lines, partial, dropped


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


def _manifest(omitted: tuple[str, ...], shown: int, total: int) -> list[str]:
    """The NOT SHOWN block. Built separately from the body because tier 5 cuts the body by
    bytes, and on a 900-claim document that cut severed this block entirely — leaving
    `ANSWER_SYSTEM` rule 2b naming a marker the model was never given, on exactly the
    documents where it matters most."""
    if not omitted:
        return []
    lines = ["", "--- NOT SHOWN IN FULL ---"]
    # The NUMBERS, because a model asked "how many embodiments are described?" counts what
    # is in front of it and does not experience that as guessing. Given the two totals it
    # can answer honestly instead. Omitted entirely when there is no description at all: a
    # claims-only document would otherwise be told "you were shown 0 of 0 paragraphs",
    # which invites it to call the document empty while it is holding twenty claims.
    if total:
        lines.append(f"You were shown {shown} of this document's {total} description paragraphs.")
    return [
        *lines,
        "You have NOT been shown all of: " + ", ".join(omitted) + ".",
        "If the answer is in one of those, say so and name it. Never guess at it.",
    ]


# Claim lines kept at each end when a tier windows the claim list. Same shape and the
# same number as `build_outline`'s `_OUTLINE_KEEP`, because it is the same idea: the ends
# of a claim set carry the independent claims and the most recently added ones.
_CLAIMS_KEEP = 10

CLAIMS_OMITTED_MARK = "[… {n} claims in the middle of the list not shown here …]"


def _claims_block(
    doc: ParsedDocument, *, first_limit: int | None, rest_limit: int | None, window: bool
) -> tuple[list[str], str | None]:
    """The claim lines, and the label to report if the list itself was cut.

    Windowing exists for one shape: a document whose CLAIMS alone fill the budget. Without
    it the description is unreachable at every tier — there is no tier in which claims
    yield to prose — so a question about the Background on a 900-claim document is answered
    from 289 claims and nothing else, which is not an answer.
    """
    if not doc.claims:
        return ["(none)"], None
    claims = doc.claims
    cut: str | None = None
    if window and len(claims) > 2 * _CLAIMS_KEEP:
        head, tail = claims[:_CLAIMS_KEEP], claims[-_CLAIMS_KEEP:]
        # Counted, never labelled by the numbers at the window edges. The parser records
        # claim numbers VERBATIM, so a document with duplicates produces "claims 3-3" and
        # one with 21 claims produces "claims 11-11" — both true of nothing. A count is
        # correct whatever the numbering does.
        missing = len(claims) - 2 * _CLAIMS_KEEP
        lines: list[str] = []
        for claim in head:
            lines += _claim_lines(claim, first_limit=first_limit, rest_limit=rest_limit)
        lines.append(CLAIMS_OMITTED_MARK.format(n=missing))
        for claim in tail:
            lines += _claim_lines(claim, first_limit=first_limit, rest_limit=rest_limit)
        return lines, f"{missing} claims in the middle of the claim list"
    lines = []
    for claim in claims:
        lines += _claim_lines(claim, first_limit=first_limit, rest_limit=rest_limit)
    return lines, cut


def _context(
    doc: ParsedDocument,
    secs: list[Section],
    pack: _Pack,
    *,
    first_limit: int | None,
    rest_limit: int | None,
    matched: bool = True,
    window_claims: bool = False,
) -> ContextView:
    before, before_partial, before_dropped = _render_region(secs, pack, after_claims=False)
    after, after_partial, after_dropped = _render_region(secs, pack, after_claims=True)

    parts = [CONTEXT_HEADER]
    parts += ["", "--- SECTIONS BEFORE THE CLAIMS ---"]
    parts += before or ["(none)"]
    if doc.claims_heading is not None:
        parts += ["", "--- CLAIMS HEADING ---", block_text(doc.claims_heading)]
    parts += ["", f"--- CLAIMS ({len(doc.claims)}) ---"]
    claim_lines, claims_cut = _claims_block(
        doc, first_limit=first_limit, rest_limit=rest_limit, window=window_claims
    )
    parts += claim_lines
    parts += ["", "--- SECTIONS AFTER THE CLAIMS ---"]
    parts += after or ["(none)"]

    # The windowed claims are reported in the same list as the sections, because from the
    # user's side they are the same fact: part of the document was not read.
    omitted = tuple(
        dict.fromkeys(before_partial + after_partial + ([claims_cut] if claims_cut else []))
    )
    # Named, in the context itself, so the model can say "that is in the Detailed
    # Description, which I was not shown" instead of guessing. The user gets the same list
    # as a warning — see `verify.partial_context_warning`.
    total = sum(len(sec.blocks) for sec in secs)
    dropped = before_dropped + after_dropped
    return ContextView(
        text="\n".join([*parts, *_manifest(omitted, total - dropped, total)]),
        omitted=omitted,
        omitted_paragraphs=dropped,
        matched=matched or not omitted,
        headed=any(s.heading for s in secs) or not secs,
    )


def build_context(
    doc: ParsedDocument,
    words: set[str] | frozenset[str] = frozenset(),
    *,
    max_chars: int = 120_000,
) -> ContextView:
    """The document as the Q&A branch reads it, scoped to the question.

    Five tiers, each evaluated only if the previous result is still too long — never a
    "shrink until it fits" loop, so the same document and question give the same bytes.
    `words` is the question's content words.

    **The default budget holds a whole 37-page patent, so tier 1 wins on every realistic
    document and no retrieval happens at all.** That is deliberate, and it is measured
    rather than assumed: at a 106,827-character context the `answer` call ran in a median
    2.3 s (n=6, min 1.6 s, max 3.5 s) — *faster* than the same questions at a 30,000-char
    budget, where a fragmented context made the model work harder and one call hit the
    12 s node timeout outright. Retrieval exists for what is above the budget, which is
    reachable by design: `max_html_chars` is 200,000.

    Tier 5 is the guarantee: the result is never longer than `max_chars`, for any
    document, including a pathological single claim of a million characters — PROVIDED
    `max_chars` leaves room for the "not shown" manifest, which is re-attached after the
    hard cut rather than being cut with the body. Below a few hundred characters the
    manifest alone exceeds the budget and the result overshoots; that is unreachable from
    `Settings`, where the smallest sane value is four orders of magnitude larger, and the
    manifest is the one thing worth overshooting for.
    """
    secs = sections(doc)
    order, matched = _rank(secs, set(words))
    everything = _Pack(
        frozenset((si, bi) for si, sec in enumerate(secs) for bi in range(len(sec.blocks)))
    )
    # Every section that survives costs a blank line and a `## label` line that no
    # paragraph's own length accounts for. Charged UP FRONT at the worst case — all of
    # them survive — because an unbudgeted overhead made every packing tier overshoot by
    # the same amount, so trimming the claims in tiers 3 and 4 handed the freed bytes
    # straight back and the document fell to tier 5's blind cut regardless.
    headings_cost = sum(len(s.label) + 4 for s in secs)

    # (claim first-block limit, claim continuation limit, "pack the sections?",
    #  "window the claim LIST?")
    tiers: tuple[tuple[int | None, int | None, bool, bool], ...] = (
        (None, None, False, False),  # 1. the whole document
        (None, None, True, False),  # 2. claims in full + the paragraphs the question needs
        (None, 200, True, False),  # 3. claim continuations trimmed, freeing budget for prose
        (600, 200, True, False),  # 4. claims trimmed too — a 900-claim document
        (600, 200, True, True),  # 5. the claim LIST windowed, so prose can exist at all
    )
    for first_limit, rest_limit, pack, window in tiers:
        if pack:
            # What the claims and the scaffolding cost with no prose at all. The section
            # budget is whatever is left, so the pack depends only on the document, the
            # question and the tier — never on how a previous tier turned out.
            empty = _context(
                doc,
                secs,
                _Pack(),
                first_limit=first_limit,
                rest_limit=rest_limit,
                window_claims=window,
            )
            budget = max(0, max_chars - len(empty.text) - headings_cost)
            packed = _pack(secs, order, budget)
        else:
            packed = everything
        view = _context(
            doc,
            secs,
            packed,
            first_limit=first_limit,
            rest_limit=rest_limit,
            matched=matched,
            window_claims=window,
        )
        if len(view.text) <= max_chars:
            return view

    # 5. Even the claims alone do not fit. Cut the BODY and re-attach the manifest, rather
    #    than cutting the string end to end: a blind byte cut severed the
    #    `--- NOT SHOWN IN FULL ---` block on exactly the documents that need it, leaving
    #    ANSWER_SYSTEM rule 2b naming a marker the model had never been given.
    omitted = view.omitted or (UNTITLED_SECTION,)
    total = sum(len(sec.blocks) for sec in secs)
    manifest = "\n".join(_manifest(omitted, total - view.omitted_paragraphs, total))
    room = max(0, max_chars - len(CONTEXT_TAIL) - len(manifest) - 1)
    body = view.text[: len(view.text) - len(manifest)] if view.omitted else view.text
    return ContextView(
        text=body[:room] + CONTEXT_TAIL + "\n" + manifest,
        omitted=omitted,
        omitted_paragraphs=view.omitted_paragraphs,
        matched=view.matched,
        headed=view.headed,
    )


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
