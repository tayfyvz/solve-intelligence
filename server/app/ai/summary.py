"""One plain sentence per operation, for the proposal card.

The card has to say what will happen in words a patent attorney reads once. Nothing here
is generated; it is a lookup keyed on the operation kind, so the card can never describe
an edit different from the one that will run.
"""

from __future__ import annotations

from collections.abc import Callable

from app.ai.document import MARK_TAGS
from app.ai.operations import MARK_WORDS
from app.ai.schemas import Op


def _format_claim(op: Op) -> str:
    word = MARK_WORDS[MARK_TAGS[op.mark]]
    if op.enabled:
        return f"Make claim {op.claim_number} {word}."
    return f"Remove {word} from claim {op.claim_number}."


def _insert_claim(op: Op) -> str:
    if op.after_claim_number == 0:
        return "Insert a new claim before claim 1."
    return f"Insert a new claim after claim {op.after_claim_number}."


def _insert_section(op: Op) -> str:
    where = "after the claims" if op.position == "after_claims" else "before the claims"
    return f'Add a "{op.heading}" section {where}.'


SUMMARIES: dict[str, Callable[[Op], str]] = {
    "format_claim": _format_claim,
    "delete_claim": lambda op: f"Delete claim {op.claim_number}.",
    "insert_claim": _insert_claim,
    "replace_claim": lambda op: f"Rewrite claim {op.claim_number}.",
    "insert_section": _insert_section,
    "replace_text": lambda op: f'Replace "{op.find}" with "{op.replace}" throughout the document.',
}


def summarise(op: Op) -> str:
    return SUMMARIES[op.kind](op)
