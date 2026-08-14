"""The Python that guards the understanding.

Pure: no `openai`, no `langgraph`, no I/O. It lives in 4A rather than 4C because
`gate_understanding` is the safety mechanism the whole understanding layer rests on, and
it needs only `document.py` and `schemas.py`.

The principle the file exists to enforce: **`gate_understanding` only ever moves an
understanding towards `resolved=False`.** It can never turn an unresolved request into a
resolved one — that direction would be a guess, and this is a legal document.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.ai.document import ParsedDocument, block_text
from app.ai.schemas import Understanding

MAX_CLARIFY_TURNS = 2  # consecutive clarifying questions before we stop asking
MAX_OPTIONS = 4
MAX_OPTION_CHARS = 80


class Turn(Protocol):
    """The shape `clarify_floor` needs, and no more.

    A structural type rather than an import of `app.schemas.ChatTurn`: that is the wire
    layer, and an engine module that imports it inverts the dependency for the sake of
    two attributes. Same argument as verify.py's (kind, ref, quote) triples.
    """

    role: str
    content: str


_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})

# `claim3` has no word boundary between the word and the digit, so the trailing \b that
# would be natural here is wrong. A negative lookahead for a letter keeps "claimed" out.
_CLAIM_TOKEN = re.compile(r"\bclaims?(?![a-z])", re.I)
_LEAD_PUNCT = re.compile(r"^\s*[.#:]?\s*")
_NEXT_TOKEN = re.compile(r"(\d{1,3})\b|([a-z]+)\b", re.I)
_SEPARATOR = re.compile(r"\s*(,|and|or|to|through|-)\s*", re.I)
_RANGE_SEPARATORS = {"to", "through", "-"}
_TRAILING_WORD = re.compile(r"(\w+)$")

_CARDINALS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
)  # fmt: skip
_ORDINALS = (
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
    "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth",
)  # fmt: skip


def _digit_ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


_WORD_NUM: dict[str, int] = {
    **{word: i for i, word in enumerate(_CARDINALS, 1)},
    **{word: i for i, word in enumerate(_ORDINALS, 1)},
    **{_digit_ordinal(i): i for i in range(1, 21)},
}


def collapse(text: str) -> str:
    """Whitespace, unicode punctuation, NULs. Nothing semantic.

    The output is never sent to the model in place of the user's words: translating
    quotes is safe, but substituting number words is not ("claim one hundred and two",
    "a one-sided claim"), and the model handles typos better than any regex we would
    write. Rewriting the instruction would also make the restatement a restatement of
    our guess rather than of the user's sentence.
    """
    return _WS.sub(" ", text.translate(_QUOTES).replace("\x00", "")).strip()


def _read_numbers(tail: str) -> list[int]:
    """Read a comma/and/range-separated run of claim numbers from the front of `tail`."""
    tail = _LEAD_PUNCT.sub("", tail)
    numbers: list[int] = []
    is_range = False
    while True:
        token = _NEXT_TOKEN.match(tail)
        if token is None:
            break
        if token.group(1) is not None:
            value = int(token.group(1))
        else:
            value = _WORD_NUM.get(token.group(2).lower(), 0)
            if not value:
                break
        if is_range and numbers:
            numbers += range(numbers[-1] + 1, value + 1)
        else:
            numbers.append(value)
        tail = tail[token.end() :]

        separator = _SEPARATOR.match(tail)
        if separator is None:
            break
        is_range = separator.group(1).lower() in _RANGE_SEPARATORS
        tail = tail[separator.end() :]
    return numbers


def claim_refs(text: str) -> list[int]:
    """Every claim number a human would read out of this sentence, in order.

    Handles "claim 3", "claim3", "claim #3", "claims 3 and 5", "claims 3-5",
    "claim three", "the 3rd claim", "the third claim".

    Deliberately does NOT handle "the last claim", "it" or "that one" — those need the
    document or the transcript, so they belong to the model, not to a regex.
    """
    text = collapse(text)
    found: list[int] = []
    for match in _CLAIM_TOKEN.finditer(text):
        numbers = _read_numbers(text[match.end() :])
        if numbers:
            found += numbers
            continue
        # "the third claim" / "the 3rd claim": the number comes before the word.
        word = _TRAILING_WORD.search(text[: match.start()].rstrip())
        if word is not None and word.group(1).lower() in _WORD_NUM:
            found.append(_WORD_NUM[word.group(1).lower()])
    return list(dict.fromkeys(found))


# ------------------------------------------------------------------- the fast path

_FAST_MARK = re.compile(r"^(?:make|set|turn)\s+claim\s+(\d{1,3})\s+(bold|italic)\s*\.?$", re.I)
_FAST_UNMARK = re.compile(r"^(?:un)?(bold|italic)\s+claim\s+(\d{1,3})\s*\.?$", re.I)
_FAST_DELETE = re.compile(r"^(?:delete|remove)\s+claim\s+(\d{1,3})\s*\.?$", re.I)


def _resolved(intent: str, restatement: str, reason: str, numbers: list[int]) -> Understanding:
    return Understanding(
        intent=intent,
        restatement=restatement,
        reason=reason,
        target_kind="claims",
        claim_numbers=numbers,
        section_heading=None,
        prior_art_role="none",
        resolved=True,
        confidence="high",
        question=None,
        options=[],
    )


def fast_understanding(
    instruction: str, doc: ParsedDocument, *, pending_question: str | None
) -> Understanding | None:
    """A deterministic understander for three unambiguous sentences.

    It is NOT a router: it returns a fully resolved Understanding, or None. Each guard
    below is a class of input where a human reading the sentence alone could still be
    wrong:

      * a pending clarifying question — the sentence must be read as an ANSWER, not a
        fresh instruction ("delete claim 3" after "which claim did you mean?" is data);
      * a claim number that does not exist in this parse;
      * anything not matching end to end.

    Everything else costs one LLM call. Anchoring makes these fail CLOSED, which is the
    right failure mode: "mak claim3 bold", "bold the 3rd claim pls" and "can u make claim
    three bold" all fall through to the model, correctly. If this ever becomes a source
    of doubt in review, deleting the function and its call site is a two-line change that
    costs ~1.5 s on exactly two of the four acceptance instructions and nothing else.
    """
    if pending_question:
        return None
    text = collapse(instruction)
    numbers = {c.number for c in doc.claims}

    match = _FAST_MARK.match(text)
    if match is not None:
        number, mark = int(match.group(1)), match.group(2).lower()
        if number not in numbers:
            return None
        return _resolved(
            "edit_ops", f"Make claim {number} {mark}.", f"fast path: {mark} claim", [number]
        )

    match = _FAST_UNMARK.match(text)
    if match is not None:
        mark, number = match.group(1).lower(), int(match.group(2))
        if number not in numbers:
            return None
        undo = text.lower().startswith("un")
        verb = "Remove" if undo else "Make"
        restatement = (
            f"Remove {mark} from claim {number}." if undo else f"Make claim {number} {mark}."
        )
        return _resolved("edit_ops", restatement, f"fast path: {verb.lower()} {mark}", [number])

    match = _FAST_DELETE.match(text)
    if match is not None:
        number = int(match.group(1))
        if number not in numbers:
            return None
        return _resolved("edit_ops", f"Delete claim {number}.", "fast path: delete claim", [number])

    return None


_FILE_WORDS = re.compile(
    r"\b(file|attachment|attached|upload(?:ed)?|prior art|"
    r"reference (?:doc|document|material)|\.txt)\b",
    re.I,
)


def missing_file_question(instruction: str, prior_art: str) -> str | None:
    """A file reference with no file is unambiguous — answer it without an LLM call.

    Deliberately false-positive-tolerant: the worst case is asking a user who wrote
    "file" for another reason to attach one, and they rephrase. It counts against
    clarify_count like any other clarification — exempting it would create a loop with
    no bound.
    """
    if prior_art.strip() or not _FILE_WORDS.search(collapse(instruction)):
        return None
    return (
        "I don't have a file to look at — nothing is attached to this conversation. "
        "Drop a .txt file onto the chat panel and ask again."
    )


# ------------------------------------------------------------------- the clarify bound

CAPABILITY_STATEMENT = (
    "I'm still not sure which part of the document you mean, so I haven't changed anything. "
    "I can: make a claim bold, italic or struck through; delete a claim; replace exact wording; "
    "rewrite or add a claim; or add a section. Try naming a claim number, for example "
    '"make claim 2 bold".'
)

WHICH_CLAIM = "I'm not sure which claim you mean. Which claim number should I change?"
_NO_CLAIMS_AT_ALL = (
    "This document doesn't have any numbered claims yet, so there's no claim for me to "
    "change. I can add one, or write a section — what would you like?"
)


def clarify_allowed(clarify_count: int) -> bool:
    return clarify_count < MAX_CLARIFY_TURNS


def resolve_outcome(u: Understanding, *, clarify_count: int) -> Understanding:
    """Convert an unresolved understanding into a TERMINAL outcome once the budget is spent.

    The model is never told about the budget; it is always allowed to be honest. A model
    told "you may not ask again" invents an answer to comply, which is the exact failure
    this layer exists to prevent. This function is where honesty stops costing the user
    another round trip.

    TERMINAL is the whole point. Rewriting `question` while leaving `resolved=False` still
    produces status="needs_clarification" on the wire; the client stores it as a pending
    question, increments its counter, the next turn clamps straight back to 2, and the
    user is handed the same capability statement forever. A bound that does not change
    the OUTCOME is not a bound.
    """
    if u.resolved or clarify_allowed(clarify_count):
        # Overwrite on BOTH paths, not just the terminal one. The model emits this field
        # (strict mode requires every field), and an early `return u` would let a model
        # that guessed True terminate a turn Python never terminated. U20 is the test.
        return u.model_copy(update={"clarify_exhausted": False})
    return u.model_copy(
        update={
            "question": CAPABILITY_STATEMENT,
            "options": [],  # nothing to click; the statement IS the guidance
            "clarify_exhausted": True,
        }
    )


def clarify_floor(history: list[Turn]) -> int:
    """How many consecutive clarifying questions the TRANSCRIPT shows we just asked.

    `clarify_count` arrives from the client and is not evidence of anything. The history
    is not evidence either — but it is the SAME evidence the model is given, so a client
    that suppresses the questions from `history` to lower this floor also removes the
    context that makes its own conversation work. Lying costs the liar.
    """
    seen = 0
    for turn in reversed(history):
        if turn.role != "assistant":
            continue
        if not turn.content.rstrip().endswith("?"):
            break  # a statement, an answer or a restatement ends the run
        seen += 1
        if seen >= MAX_CLARIFY_TURNS:
            break
    return seen


def _clean_options(options: list[str]) -> list[str]:
    cleaned = [collapse(o) for o in options]
    kept = [o for o in cleaned if o and len(o) <= MAX_OPTION_CHARS]
    return list(dict.fromkeys(kept))[:MAX_OPTIONS]


def _no_such_claim(unknown: list[int], n: int) -> str:
    if n == 0:
        return _NO_CLAIMS_AT_ALL
    which = ", ".join(str(x) for x in unknown)
    return (
        f"There is no claim {which} in this document — it has {n} "
        f"claim{'s' if n != 1 else ''}, numbered 1 to {n}. Which one did you mean?"
    )


def gate_understanding(
    u: Understanding, doc: ParsedDocument, instruction: str, *, clarify_count: int
) -> Understanding:
    """Everything Python knows that the model might have got wrong.

    Runs on EVERY path, including the fast path, immediately after `understand` and
    BEFORE any branch is chosen.
    """
    numbers = {c.number for c in doc.claims}

    # 1. Low confidence never acts. A model that says confidence="low", resolved=True is
    #    self-contradictory, and the contradiction is exactly the case a prompt-only rule
    #    fails on. The reverse — confidence="high", resolved=False — is left alone: an
    #    honest question is always allowed.
    if u.confidence == "low":
        u = u.model_copy(update={"resolved": False, "question": u.question or WHICH_CLAIM})

    # 2. Every resolved claim number must exist in THIS parse. This runs before the
    #    branch, so a nonexistent claim number CANNOT REACH plan_ops or draft — not "is
    #    rejected by them".
    unknown = [n for n in u.claim_numbers if n not in numbers]
    if unknown:
        u = u.model_copy(
            update={
                "resolved": False,
                "claim_numbers": [],  # nothing downstream may use them
                "question": _no_such_claim(unknown, len(doc.claims)),
                "options": [],
            }
        )

    # 3. A claim-targeted edit with no target is not actionable.
    if u.intent in ("edit_ops", "generate") and u.target_kind == "claims" and not u.claim_numbers:
        u = u.model_copy(update={"resolved": False, "question": u.question or WHICH_CLAIM})

    # 4. A claim request against a document with no claims.
    if u.target_kind == "claims" and not doc.claims:
        u = u.model_copy(update={"resolved": False, "options": [], "question": _NO_CLAIMS_AT_ALL})

    # 5. Options hygiene. The live pre-flight measured a real `understand` call returning
    #    resolved=True, confidence="high" AND a populated options list; the panel would
    #    have rendered those as clarification buttons under a bubble saying the work was
    #    done. The prompt now forbids it, and this is what makes the prompt's compliance
    #    irrelevant.
    u = u.model_copy(update={"options": [] if u.resolved else _clean_options(u.options)})

    # 6. Budget: after K consecutive questions, state the capability and stop asking.
    return resolve_outcome(u, clarify_count=clarify_count)


def quote_target(doc: ParsedDocument, numbers: list[int], *, chars: int = 60) -> str:
    """The first few words of the claim we actually resolved, taken from the parse.

    This is the sentence that catches an off-by-one: a user who asked for "the third
    claim" and reads back text they do not recognise knows immediately. It doubles as the
    pronoun antecedent for the next turn, so one string does two jobs. Taken from the
    document, so it cannot be hallucinated — U17 asserts it is a substring.
    """
    for number in numbers:
        claim = next((c for c in doc.claims if c.number == number), None)
        if claim is not None and claim.blocks:
            return block_text(claim.blocks[0])[:chars].rstrip()
    return ""
