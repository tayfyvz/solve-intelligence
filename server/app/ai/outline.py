"""Plain-text views of a parsed document, for callers with different needs.

`build_outline` is what the planner reads to UNDERSTAND a document; `build_context`,
`build_spec`, `claims_excerpt` and `section_excerpt` are what the generating nodes read
to WRITE one. An outline truncated at 240 characters cannot answer "what does claim 4
depend on?", and cannot support rewriting a claim either.

`build_context` additionally decides **which parts of the document the question needs**.
On a patent too long for any sane budget the non-claim paragraphs are ranked by lexical
overlap with the question, packed to the budget, rendered in document order, and whatever
did not fit is NAMED — inline and in a manifest — so the user is never told an answer is
complete when it is not.

All of these emit plain text and never HTML: the model must not start thinking in markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.document import HEADING_TAGS, Block, ParsedDocument, block_text

OUTLINE_HEADER = "DOCUMENT OUTLINE (reference only — do not copy it back)"
CONTEXT_HEADER = "DOCUMENT CONTEXT (reference only, do not copy it back)"
CONTEXT_TAIL = "\n… (context truncated — the document is longer than shown) …"

_OUTLINE_LIMITS = (240, 120, 60)
_OUTLINE_KEEP = 10  # claim lines kept at each end when the last tier drops the middle

# The three per-view ceilings, named rather than left as bare keyword defaults so that
# `prompts.worst_case_prompt_chars` can add them up. A budget nothing can total is not a
# budget; it is four numbers that happen to be small.
MAX_OUTLINE_CHARS = 8_000
MAX_CLAIMS_EXCERPT_CHARS = 30_000
MAX_SECTION_EXCERPT_CHARS = 30_000

# The label a section with no heading gets. It has to read as a place a user could point
# at, because it is quoted back to them in the "not shown" warning.
UNTITLED_SECTION = "the opening text (no heading)"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _headings(blocks: list[Block]) -> list[str]:
    return [block_text(b) for b in blocks if b.tag in HEADING_TAGS]


_PSEUDO_HEADING_MAX_WORDS = 8
# Any of . ! ? : ; ends a clause far more often than a hand-typed heading, so a block
# carrying one reads as a sentence rather than a section name.
_PSEUDO_HEADING_END_RE = re.compile(r"[.!?:;]\s*$")


def _reads_as_heading(block: Block) -> bool:
    """A plain paragraph shaped like a hand-typed section header rather than prose.

    A user adding a section rarely reaches for the editor's H1/H2/H3 buttons — they
    select a short line and press Cmd+B, which parses as a `<p>` with a whole-block bold
    mark, not a heading tag. `parse()` then folds that paragraph into the preceding claim
    as a continuation block, because from pure structure a short bold line is
    indistinguishable from a claim's own continuation prose.

    Three signals must agree, because none is safe alone in patent prose: the block's
    only mark is a whole-block bold, it runs to at most eight words, and it has no
    sentence-ending punctuation. This function is read-only — a false positive costs one
    extra honest line in the outline, never a structural change.
    """
    if block.tag != "p" or block.marks != ("strong",):
        return False
    text = block_text(block)
    if not text or _PSEUDO_HEADING_END_RE.search(text):
        return False
    return len(text.split()) <= _PSEUDO_HEADING_MAX_WORDS


def _embedded_headings(blocks: list[Block]) -> list[str]:
    """Hand-typed pseudo-headings hiding in a run of body blocks, in order.

    Presentation only: it moves nothing and changes no structure. It exists because the
    outline is the ONLY thing `understand` reads, and its rule is "the outline does not
    list it" means "the document does not contain it" — so a section the user typed by
    hand was invisible, and `understand` dead-ended on a question about text that
    `build_context` would have retrieved perfectly well.
    """
    return [block_text(b) for b in blocks if _reads_as_heading(b)]


def _annotate_pseudo_headings(line: str, embedded: list[str], limit: int) -> str:
    if not embedded:
        return line
    shown = ", ".join(f'"{_truncate(h, limit)}"' for h in embedded)
    return f"{line} (also, text with no heading tag that reads as its own heading: {shown})"


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
            line = _annotate_pseudo_headings(line, _embedded_headings(claim.blocks[1:]), limit)
        claim_lines.append(line)

    if drop_middle and len(claim_lines) > 2 * _OUTLINE_KEEP:
        first_omitted = doc.claims[_OUTLINE_KEEP].number
        last_omitted = doc.claims[-_OUTLINE_KEEP - 1].number
        claim_lines = [
            *claim_lines[:_OUTLINE_KEEP],
            f"  … (claims {first_omitted}–{last_omitted} omitted) …",
            *claim_lines[-_OUTLINE_KEEP:],
        ]

    before_line = _annotate_pseudo_headings(
        "Sections before the claims: " + (", ".join(before) if before else "(none)"),
        _embedded_headings(doc.preamble),
        limit,
    )
    after_line = _annotate_pseudo_headings(
        "Sections after the claims: " + (", ".join(after) if after else "(none)"),
        _embedded_headings(doc.postamble),
        limit,
    )

    return "\n".join(
        [
            OUTLINE_HEADER,
            before_line,
            f"Claims: {len(doc.claims)}",
            *claim_lines,
            after_line,
        ]
    )


def build_outline(doc: ParsedDocument, *, max_chars: int = MAX_OUTLINE_CHARS) -> str:
    """A one-line-per-claim map of the document, for the planner.

    Four tiers, each evaluated only if the previous result is still too long — never a
    "shrink until it fits" loop, so the same document always produces the same string.
    """
    for limit in _OUTLINE_LIMITS:
        out = _outline(doc, limit, drop_middle=False)
        if len(out) <= max_chars:
            return out
    return _outline(doc, _OUTLINE_LIMITS[-1], drop_middle=True)


# ------------------------------------------------------- words, for question scoping

_WORD_RE = re.compile(r"[a-z0-9]+")

# A short list of English inflections and nothing else. It folds "volumes"/"volume" and
# "oxygenating"/"oxygenate" together — the mismatch a user actually hits when their
# question and the patent describe the same thing in different grammatical forms.
#
# `-er`/`-ly`/`-est` are DELIBERATELY ABSENT. They were tried and removed: they merge
# "prime"/"primer" (a PCR primer is not priming) while splitting "filters"→"filt" from
# "filtering"→"filter" — a false match and a miss from one suffix family.
_SUFFIXES = ("ing", "ed")
_MIN_STEM = 4  # below this, stripping turns "gas" into "ga"
# The trailing `e` measures against a LOWER floor, which is what folds "gases" onto "gas".
_MIN_Y_STEM = 3


def stem(word: str) -> str:
    """Fold one English inflection off a word, for scoring only.

    The trailing `e` is the point: without it "volumes" folds to "volume" while "volume"
    stays "volume", so the two forms still miss each other. Stripping it from both lands
    them on "volum".

    A stem is a sort key and nothing else — it never reaches the model, the document or a
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
    """The scoring key for a piece of text. Both sides of every comparison go through
    this, so the folding only has to be CONSISTENT, not linguistically correct."""
    return {stem(word) for word in _WORD_RE.findall(text.lower())}


# ~40 common English words, kept as a literal: a dependency for forty strings is a joke.
# Raw, and subtracted BEFORE stemming — see `content_tokens`.
STOPWORDS = frozenset(
    """a an and are as at be but by can could do does for from has have how i if in
    into is it its me my of on or please should so than that the their them then there
    these they this to was were what when where which who will with would you your""".split()
)


def content_tokens(text: str) -> set[str]:
    """The question's content words, as scoring keys.

    Stopwords are removed BEFORE stemming, and the order is the whole point. Stemming
    first and subtracting after deletes any content word whose stem collides with a
    stopword's — "shoulder" folds to "should" — so a question about the shoulder of a
    housing was answered "none of the words in your question appear in this document"
    with the word sitting right there.
    """
    return {stem(word) for word in _WORD_RE.findall(text.lower()) if word not in STOPWORDS}


# --------------------------------------------------------------------- the sections


@dataclass(frozen=True)
class Section:
    """A heading and the body blocks under it, from the NON-claim regions.

    The claims are not sections: they have their own model, their own numbering rules and
    their own view. This type describes the specification around them, which is where a
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
    order. A document with no headings yields one untitled section holding everything."""
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

# A heading match is a TIEBREAK, not a multiplier. Uncapped, the bonus scaled with the
# heading's length, so "what is the priming volume in the detailed description of the
# preferred embodiments?" gave every filler paragraph under that heading a score of 4
# while the paragraph actually containing the answer scored 2 and never made the cut.
_HEADING_BONUS_CAP = 1


def _rank(secs: list[Section], words: set[str]) -> tuple[list[Ref], bool]:
    """(section, paragraph) pairs most relevant first, and whether ANYTHING matched.

    A paragraph scores on its own overlap with the question plus a capped bonus if the
    question's words hit its section heading.

    When nothing scores — the question's vocabulary differs from the document's, or the
    question is "summarise this" — the fallback is round-robin ACROSS sections rather
    than document order, which would answer "summarise this" from the front of the first
    section and omit the section literally called Summary. Both keys are total orders, so
    the same document and question always give the same ranking.
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
    # The top-ranked paragraph, when it alone was bigger than the whole budget:
    # (section, paragraph, characters kept). Never more than one.
    clipped: tuple[int, int, int] | None = None


# Below this there is no room for a clip worth reading, so the ordinary skip stands.
_MIN_CLIP_CHARS = 200


def _pack(secs: list[Section], order: list[Ref], budget: int) -> _Pack:
    """Take paragraphs in rank order while they fit.

    Two rules, and the second exists because the first alone breaks a promise:

    1. A paragraph too big for the REMAINING budget is skipped rather than ending the
       walk, so one 9,000-character paragraph cannot shut out twenty short ones behind it.
    2. **Except the top-ranked one.** Skipping it drops the only paragraph that mentions
       the question at all, fills the budget with prose that scored zero, and then tells
       the user to "ask about that section by name" — advice that provably cannot work,
       because asking again produces the same skip. It is clipped instead.
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


# The inline elision marker. Bracketed, like the `[4]` claim prefix, so it reads as this
# program's scaffolding rather than the document's own words — and `verify.py` recognises
# the shape, so a model that quotes one gets an actionable sentence about a partly-read
# document instead of an accusation of inventing a quotation.
OMITTED_MARK = "[… {n} paragraph{s} not shown here …]"
CLIPPED_MARK = "[… the rest of this paragraph is not shown here …]"


def _elision(n: int) -> str:
    return OMITTED_MARK.format(n=n, s="" if n == 1 else "s")


@dataclass(frozen=True)
class ContextView:
    """What the answer node is shown, and what it was not shown.

    `omitted` is the whole point of the type: a string alone cannot tell the caller that
    a question about the Detailed Description was answered from the claims.
    """

    text: str
    omitted: tuple[str, ...] = ()  # labels of sections not shown IN FULL
    omitted_paragraphs: int = 0
    # False when NOTHING in the question matched, so what was kept was chosen by position
    # rather than relevance. Without this the user is told "I did not see all of X" —
    # literally true and completely misleading, because it implies what they WERE shown
    # was the relevant part.
    matched: bool = True
    # False when the document has no headings at all. "Ask about a section by name" is
    # unfollowable then, and a user who tries makes the retrieval worse.
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
                    text = text[: clip[2]].rstrip() + " " + CLIPPED_MARK
                body.append(text)
            else:
                run += 1
        if run:
            body.append(_elision(run))
        dropped += sum(1 for bi in range(len(section.blocks)) if bi not in shown)
        # A clipped paragraph is shown but NOT in full, so its section is still partial.
        clipped_here = clip is not None and clip[0] == si
        if shown or not section.blocks:
            lines += ["", f"## {section.label}", *body]
        else:
            # Nothing of it survived. Naming it here as well as in the manifest would give
            # the model a heading with no text under it, which reads as "this section is
            # empty" rather than "you have not been shown it".
            partial.append(section.label)
            continue
        if clipped_here or any(bi not in shown for bi in range(len(section.blocks))):
            partial.append(section.label)
    return lines, partial, dropped


def _claim_lines(claim, *, first_limit: int | None, rest_limit: int | None) -> list[str]:
    # The bracket form [N], not "N.", so context echoed back by the model can never be
    # mistaken for a claim prefix by CLAIM_PREFIX_RE.
    head = block_text(claim.blocks[0])
    lines = [f"[{claim.number}] " + (_truncate(head, first_limit) if first_limit else head)]
    for block in claim.blocks[1:]:
        body = block_text(block)
        lines.append("    " + (_truncate(body, rest_limit) if rest_limit else body))
    return lines


def _manifest(omitted: tuple[str, ...], shown: int, total: int) -> list[str]:
    """The NOT SHOWN block. Built separately from the body because the last tier cuts the
    body by bytes, and on a 900-claim document that cut severed this block entirely."""
    if not omitted:
        return []
    lines = ["", "--- NOT SHOWN IN FULL ---"]
    # The numbers, because a model asked "how many embodiments are described?" counts
    # what is in front of it and does not experience that as guessing. Omitted entirely
    # when there is no description: "you were shown 0 of 0 paragraphs" invites the model
    # to call the document empty while it is holding twenty claims.
    if total:
        lines.append(f"You were shown {shown} of this document's {total} description paragraphs.")
    return [
        *lines,
        "You have NOT been shown all of: " + ", ".join(omitted) + ".",
        "If the answer is in one of those, say so and name it. Never guess at it.",
    ]


# Claim lines kept at each end when a tier windows the claim list. Same idea as
# `_OUTLINE_KEEP`: the ends carry the independent claims and the most recent ones.
_CLAIMS_KEEP = 10

CLAIMS_OMITTED_MARK = "[… {n} claims in the middle of the list not shown here …]"


def _claims_block(
    doc: ParsedDocument, *, first_limit: int | None, rest_limit: int | None, window: bool
) -> tuple[list[str], str | None]:
    """The claim lines, and the label to report if the list itself was cut.

    Windowing exists for one shape: a document whose CLAIMS alone fill the budget.
    Without it the description is unreachable at every tier, so a question about the
    Background on a 900-claim document is answered from 289 claims and nothing else.
    """
    if not doc.claims:
        return ["(none)"], None
    claims = doc.claims
    if window and len(claims) > 2 * _CLAIMS_KEEP:
        head, tail = claims[:_CLAIMS_KEEP], claims[-_CLAIMS_KEEP:]
        # Counted, never labelled by the numbers at the window edges: the parser records
        # claim numbers verbatim, so a document with duplicates produces "claims 3-3".
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
    return lines, None


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

    # Windowed claims are reported in the same list as the sections, because from the
    # user's side they are the same fact: part of the document was not read.
    omitted = tuple(
        dict.fromkeys(before_partial + after_partial + ([claims_cut] if claims_cut else []))
    )
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

    The shipped budget holds a whole 37-page patent, so tier 1 wins on every realistic
    document and no retrieval happens at all. Retrieval exists for what is above the
    budget, which is reachable by design because `max_html_chars` is 200,000.

    Tier 5 is the guarantee: the result is never longer than `max_chars`, for any
    document — provided `max_chars` leaves room for the manifest, which is re-attached
    after the hard cut rather than being cut with the body.
    """
    secs = sections(doc)
    order, matched = _rank(secs, set(words))
    everything = _Pack(
        frozenset((si, bi) for si, sec in enumerate(secs) for bi in range(len(sec.blocks)))
    )
    # Every surviving section costs a blank line and a `## label` line that no paragraph's
    # own length accounts for. Charged up front at the worst case: unbudgeted, this
    # overhead made every packing tier overshoot by the same amount, so trimming the
    # claims handed the freed bytes straight back and the document fell to tier 5 anyway.
    headings_cost = sum(len(s.label) + 4 for s in secs)

    # (claim first-block limit, claim continuation limit, pack the sections?,
    #  window the claim LIST?)
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

    # Even the claims alone do not fit. Cut the BODY and re-attach the manifest.
    return _hard_cut(view, secs, max_chars=max_chars)


def _hard_cut(view: ContextView, secs: list[Section], *, max_chars: int) -> ContextView:
    """The last resort every tiered view shares: cut the body by bytes, then re-attach
    the manifest.

    A blind end-to-end byte cut severed the "NOT SHOWN IN FULL" block on exactly the
    documents that need it — the ones so long that the tiers all overshot. Rebuilding the
    manifest after the cut is what keeps invariant 11 true at the one size where it is
    hardest to keep.
    """
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


# ------------------------------------------------------- the specification, for editing

SPEC_HEADER = (
    "SPECIFICATION BODY — the document's non-claim text (reference only, do not copy it "
    "back). Quote from it verbatim when you need a `find` string."
)


def _spec(doc: ParsedDocument, secs: list[Section], pack: _Pack, *, matched: bool) -> ContextView:
    """`_context` without the claims. Same regions, same elision markers, same manifest.

    The claims are deliberately absent: every caller of `build_spec` already holds
    `claims_excerpt`, which carries the claims it is about to rewrite IN FULL. Rendering
    them twice would spend the budget on text the model is already looking at.
    """
    before, before_partial, before_dropped = _render_region(secs, pack, after_claims=False)
    after, after_partial, after_dropped = _render_region(secs, pack, after_claims=True)

    parts = [SPEC_HEADER]
    parts += ["", "--- SECTIONS BEFORE THE CLAIMS ---"]
    parts += before or ["(none)"]
    parts += ["", "--- SECTIONS AFTER THE CLAIMS ---"]
    parts += after or ["(none)"]

    omitted = tuple(dict.fromkeys(before_partial + after_partial))
    total = sum(len(sec.blocks) for sec in secs)
    dropped = before_dropped + after_dropped
    return ContextView(
        text="\n".join([*parts, *_manifest(omitted, total - dropped, total)]),
        omitted=omitted,
        omitted_paragraphs=dropped,
        matched=matched or not omitted,
        headed=any(s.heading for s in secs) or not secs,
    )


def build_spec(
    doc: ParsedDocument,
    words: set[str] | frozenset[str] = frozenset(),
    *,
    max_chars: int = 200_000,
) -> ContextView:
    """The specification body as the EDITING nodes read it.

    `plan_ops` can emit `replace_text`, which is document-wide, literal and case
    sensitive — so a model that has never seen the description has to invent the `find`
    string it is matching against. `draft` has the same problem one step further on:
    prose written without the surrounding specification uses terminology the document
    does not.

    Two tiers, evaluated not looped, exactly as `build_context` does it:

    1. the whole specification, which is what every document this app accepts reaches —
       `max_chars` defaults at `max_html_chars`, and the sections are a strict subset of
       the HTML they were parsed from;
    2. the paragraphs this instruction scores against, packed to the budget, with
       everything else named.

    Tier 1 does not read `words` at all, so on the normal path the rendered text is a
    pure function of the DOCUMENT — identical from turn to turn, which is what lets the
    prompt prefix stay cached across a conversation. Tier 2 is question-scoped and
    therefore cache-cold by nature; that is the price of a document too big to show, and
    it is only reachable below the default budget.

    Returns an empty view for a document with no specification text at all — which is
    both seed patents, since they are pure claim sets — so callers can omit the block
    rather than emit a header with "(none)" twice under it.

    Bounded by `max_chars` on the same terms as `build_context`: provided the budget
    leaves room for the manifest, which is re-attached AFTER the hard cut rather than
    cut with the body. Below that the manifest wins and the result overshoots, because
    naming what was dropped (invariant 11) matters more than the last hundred bytes.
    """
    secs = sections(doc)
    if not any(sec.blocks for sec in secs):
        return ContextView(text="", headed=any(s.heading for s in secs) or not secs)

    order, matched = _rank(secs, set(words))
    everything = _Pack(
        frozenset((si, bi) for si, sec in enumerate(secs) for bi in range(len(sec.blocks)))
    )
    view = _spec(doc, secs, everything, matched=matched)
    if len(view.text) <= max_chars:
        return view

    # Same accounting as `build_context`: charge the scaffolding and the `## label` lines
    # up front, so the pack depends only on the document and the instruction.
    headings_cost = sum(len(s.label) + 4 for s in secs)
    empty = _spec(doc, secs, _Pack(), matched=matched)
    budget = max(0, max_chars - len(empty.text) - headings_cost)
    view = _spec(doc, secs, _pack(secs, order, budget), matched=matched)
    if len(view.text) <= max_chars:
        return view
    return _hard_cut(view, secs, max_chars=max_chars)


def claims_excerpt(
    doc: ParsedDocument, numbers, *, max_chars: int = MAX_CLAIMS_EXCERPT_CHARS
) -> str:
    """The full text of selected claims — the view every generating node reads.

    Never truncated per claim: truncating the very text a node is about to rewrite is
    what made a model refuse and ask to be shown the claim. An empty or fully-unknown set
    returns "", and the caller omits the block rather than emitting an empty header.
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


def section_excerpt(
    doc: ParsedDocument, heading: str | None, *, max_chars: int = MAX_SECTION_EXCERPT_CHARS
) -> str:
    """The full text of one named non-claim section, for a generating node whose request
    `understand` resolved to a section rather than to claims.

    Mirrors `claims_excerpt`: matched by heading text only, case-insensitively, and "" for
    no match — so callers can call it unconditionally. Without it, "make the Appendix more
    professional" reached `draft` with nothing but the outline's heading list, and the
    model asked the user to paste the text back in.
    """
    if not heading:
        return ""
    match = next(
        (s for s in sections(doc) if s.label.strip().lower() == heading.strip().lower()), None
    )
    if match is None or not match.blocks:
        return ""
    lines = [f"RELEVANT SECTION, IN FULL — {match.label}"]
    lines += [block_text(b) for b in match.blocks]
    out = "\n\n".join(lines)
    if len(out) <= max_chars:
        return out
    return out[: max(0, max_chars - len(CONTEXT_TAIL))] + CONTEXT_TAIL
