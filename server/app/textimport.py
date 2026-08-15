"""Plain text in, editor HTML out. One pure function, for importing a patent someone
already wrote.

It lives on the server because the shape of a claim is defined by `ai/document.py`'s
parser, and a second definition in TypeScript would drift from the first. The importer
ends by running that parser and renderer over its own output, so what it returns is by
construction in the canonical form the rest of the app stores.

Everything it could not be sure about comes back as a `note`. This file never guesses in
silence: a claim set with duplicate numbers, a file with no claims and a file that is not
a patent at all are all legal inputs here, and all three say what happened.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.ai.document import CLAIM_PREFIX_RE, escape_text, parse, render
from app.sanitize import sanitize_html

MAX_TITLE_CHARS = 200  # matches models.MAX_TITLE_LENGTH; a longer first line is body text

# A heading is short, has no sentence-ending punctuation, and is not a claim. The two
# arms are deliberately different in kind: a closed list of the sections a patent
# actually has, plus a shape rule for anything else. The list alone would miss "TECHNICAL
# FIELD OF THE DISCLOSURE"; the shape rule alone would promote every short line.
KNOWN_HEADINGS = frozenset(
    {
        "abstract",
        "background",
        "background of the invention",
        "brief description of the drawings",
        "brief summary",
        "claims",
        "cross reference to related applications",
        "detailed description",
        "detailed description of the invention",
        "description",
        "description of related art",
        "field",
        "field of the invention",
        "prior art",
        "summary",
        "summary of the invention",
        "technical field",
        "what is claimed is",
        "what is claimed is:",
    }
)
MAX_HEADING_CHARS = 80

_BOM = "﻿"
_CLAIMS_HEADING_RE = re.compile(r"^\s*(claims?|what is claimed is)\b", re.I)


@dataclass
class ImportResult:
    """What the importer made of the file, and what it was unsure about.

    `notes` is not decoration: an import that silently reinterprets someone's claim set
    is worse than one that refuses, so every judgement call appears here in a sentence
    the user reads before committing to the result.
    """

    title: str
    html: str
    claim_count: int
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- normalisation


def normalise(raw: str) -> str:
    """One leading BOM, CRLF/CR, tabs and control characters. Nothing semantic.

    Blank lines survive, because they are the paragraph separator everything below
    depends on, and so does interior indentation inside a line.
    """
    text = raw.removeprefix(_BOM).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    # Category "C" is control/format/surrogate/private-use. Newlines are kept by name;
    # everything else in that class is invisible in the editor and corrupts anything
    # that later logs or exports the document. Emoji and RTL text are categories S and L
    # and pass through untouched — a patent may legitimately quote either.
    return "".join(c for c in text if c == "\n" or unicodedata.category(c)[0] != "C")


def _looks_like_heading(line: str) -> bool:
    folded = " ".join(line.casefold().split()).rstrip(":")
    if folded in KNOWN_HEADINGS:
        return True
    if len(line) > MAX_HEADING_CHARS or CLAIM_PREFIX_RE.match(line):
        return False
    stripped = line.strip()
    # An all-caps line with no full stop is the other form every patent office uses.
    return bool(stripped) and stripped == stripped.upper() and not stripped.endswith(".")


def _paragraphs(text: str) -> list[str]:
    """Blank-line-separated blocks, with hard wrapping undone inside each one.

    A line that is itself a heading or a new claim ENDS the block it would otherwise have
    been joined onto — a plain-text patent frequently separates claims with a single
    newline rather than a blank one, and joining them would produce one paragraph holding
    sixty claims, which parses as no claims at all.
    """
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(" ".join(line.split()) for line in current).strip())
            current.clear()

    for line in text.split("\n"):
        if not line.strip():
            flush()
            continue
        if current and (_looks_like_heading(line) or CLAIM_PREFIX_RE.match(line.strip())):
            flush()
        current.append(line)
        if _looks_like_heading(line):
            flush()  # a heading is a block of exactly one line
    flush()
    return [p for p in out if p]


# ------------------------------------------------------------------- the conversion


def _title_of(paragraphs: list[str]) -> tuple[str, list[str]]:
    """Take the first paragraph as the title when it plausibly is one.

    `getHTML()` returns body content only, so a title left in the content column is
    destroyed on the first save. It goes to `Document.title` or it does not exist.
    """
    if not paragraphs:
        return "", paragraphs
    first = paragraphs[0]
    plausible = (
        len(first) <= MAX_TITLE_CHARS
        and not first.endswith(".")
        and not CLAIM_PREFIX_RE.match(first)
        and not _CLAIMS_HEADING_RE.match(first)
    )
    return (first, paragraphs[1:]) if plausible else ("", paragraphs)


def _claim_numbers(paragraphs: list[str]) -> list[int]:
    return [int(m.group(1)) for p in paragraphs if (m := CLAIM_PREFIX_RE.match(p))]


def _numbering_notes(numbers: list[int]) -> list[str]:
    """Say what is odd about the numbering; never repair it.

    Renumbering on import would rewrite a legal document nobody asked us to touch, and
    the cross-references would then point at the old numbers. The applier's "renumber
    once, then remap" is the only thing allowed to change a claim number.
    """
    notes: list[str] = []
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        listed = ", ".join(str(n) for n in duplicates)
        notes.append(
            f"Claim number{'s' if len(duplicates) > 1 else ''} {listed} "
            f"appear{'' if len(duplicates) > 1 else 's'} more than once. "
            "The numbering was imported exactly as written and not corrected."
        )
    gaps = [(a, b) for a, b in zip(numbers, numbers[1:], strict=False) if b > a + 1]
    if gaps:
        listed = ", ".join(f"{a} to {b}" for a, b in gaps[:3])
        notes.append(f"The claim numbering skips ({listed}). It was imported as written.")
    if numbers and numbers != sorted(numbers):
        notes.append("The claims are not in ascending order. They were imported as written.")
    return notes


def _clean_and_canonicalise(html: str) -> str:
    """Sanitise FIRST, canonicalise second. The other order stores bytes the save path's
    own sanitiser would then change, so what the user previewed and what the database
    holds would differ. `render(parse(x))` is the canonical form, so the stored bytes are
    already what the parser reads back."""
    return render(parse(sanitize_html(html)))


def import_text(raw: str, *, fallback_title: str = "Imported patent") -> ImportResult:
    """Convert a plain-text patent into the HTML shape the rest of the app stores.

    Never raises on content: an empty file, a shopping list and a 1 MB claim set are all
    handled, and what could not be recognised is reported in `notes`. Whether an empty
    result deserves a 422 is an HTTP question, so the caller decides it.
    """
    paragraphs = _paragraphs(normalise(raw))
    title, body = _title_of(paragraphs)
    notes: list[str] = []

    numbers = _claim_numbers(body)
    has_claims_heading = any(_CLAIMS_HEADING_RE.match(p) and _looks_like_heading(p) for p in body)

    # The parser needs either a Claims heading or a run of >=2 numbered paragraphs. When
    # there is a run but no heading, supplying one makes the claims region explicit and
    # stable instead of leaving it to the fallback rule — and the note says we did it.
    if len(numbers) >= 2 and not has_claims_heading:
        first = next(i for i, p in enumerate(body) if CLAIM_PREFIX_RE.match(p))
        body = [*body[:first], "Claims", *body[first:]]
        notes.append(
            f'No "Claims" heading was found, so one was added before claim {numbers[0]}. '
            f"{len(numbers)} numbered paragraphs were read as claims."
        )

    html = "".join(
        f"<h1>{escape_text(p)}</h1>" if _looks_like_heading(p) else f"<p>{escape_text(p)}</p>"
        for p in body
    )
    html = _clean_and_canonicalise(html)

    parsed = parse(html)
    if len(numbers) == 1:
        notes.append(
            "Only one numbered paragraph was found, so it was imported as ordinary text "
            "rather than as a claim. Two or more are needed to recognise a claim set."
        )
    if not parsed.claims:
        if not body:
            notes.append("That file had no readable text, so the patent was created empty.")
        elif len(numbers) != 1:
            notes.append(
                "No numbered claims were recognised, so the whole file was imported as "
                "body text. You can add claims in the editor or ask the AI to."
            )
    notes += _numbering_notes([c.number for c in parsed.claims])

    return ImportResult(
        title=title or fallback_title,
        html=html,
        claim_count=len(parsed.claims),
        notes=notes,
    )
