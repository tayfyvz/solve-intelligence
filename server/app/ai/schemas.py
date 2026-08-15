"""Contracts between the language model and the deterministic engine.

Two rules hold the file together:

- **No field constraints on a planner-facing model.** `Field(ge=…)`, `min_length` and
  friends make `to_strict_json_schema` emit JSON-schema keywords whose acceptance we
  cannot verify without a key, so every bound is enforced in Python by `require()`.
- **Never imports `openai` or `langgraph`.** The engine stays testable with no key.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, StrictInt

OpKind = Literal[
    "format_claim",
    "delete_claim",
    "insert_claim",
    "replace_claim",
    "insert_section",
    "delete_section",
    "replace_text",
]


class Op(BaseModel):
    """One flat model with optional fields, not a discriminated union: a flat model
    produces one obvious schema, and per-kind validation has to happen in Python anyway
    (`REQUIRED` and `require` below)."""

    kind: OpKind
    # StrictInt on both: lax coercion accepts `true` as 1 and `"3"` as 3, so a malformed
    # operation or a tampered proposal would silently DELETE a real claim.
    claim_number: StrictInt | None = None
    after_claim_number: StrictInt | None = None  # 0 = before claim 1
    mark: Literal["bold", "italic", "strike"] | None = None
    enabled: bool | None = None
    text: str | None = None
    heading: str | None = None
    paragraphs: list[str] | None = None
    position: Literal["before_claims", "after_claims"] | None = None
    find: str | None = None
    replace: str | None = None


class EditPlan(BaseModel):
    status: Literal["ok", "needs_clarification"]
    message: str
    operations: list[Op]


class PlanError(ValueError):
    """A structurally valid plan the engine cannot act on."""


class LlmUnavailable(RuntimeError):
    """The model returned nothing usable. Carries a message a user could read.

    Defined here rather than in `llm.py` because `nodes.node_guard` has to catch it and
    `nodes.py` must not import `openai`.
    """


REQUIRED: dict[str, tuple[str, ...]] = {
    "format_claim": ("claim_number", "mark", "enabled"),
    "delete_claim": ("claim_number",),
    "insert_claim": ("after_claim_number", "text"),
    "replace_claim": ("claim_number", "text"),
    "insert_section": ("heading", "paragraphs", "position"),
    "delete_section": ("heading",),
    "replace_text": ("find", "replace"),
}

MAX_CLAIM_NUMBER = 999
MAX_SECTION_PARAGRAPHS = 20


def require(op: Op) -> None:
    """Per-kind validation, and the only pre-apply validation of an operation. Bounds
    live here rather than in the schema."""
    # A bare subscript on purpose: `op.kind` is a validated Literal, so a KeyError is
    # impossible unless someone adds a kind without a REQUIRED row — which a test catches.
    missing = [f for f in REQUIRED[op.kind] if getattr(op, f) is None]
    if missing:
        raise PlanError(f"The AI's {op.kind} instruction was missing: {', '.join(missing)}.")
    for field_name in ("claim_number", "after_claim_number"):
        value = getattr(op, field_name)
        if value is not None and not (0 <= value <= MAX_CLAIM_NUMBER):
            raise PlanError(f"Claim number {value} is out of range.")
    if op.paragraphs is not None and len(op.paragraphs) > MAX_SECTION_PARAGRAPHS:
        raise PlanError("That section had too many paragraphs.")


# ------------------------------------------------------------------- understanding

Intent = Literal["edit_ops", "generate", "answer"]

TargetKind = Literal[
    "claims",  # one or more numbered claims
    "section",  # a named non-claim section (Background, Abstract, …)
    "selection",  # the text the user has highlighted in the editor
    "whole_document",  # "summarise this", "check the whole thing"
    "prior_art",  # the uploaded .txt only
    "none",  # no target could be determined
]

PriorArtRole = Literal[
    "none",  # the file is irrelevant to this request (or absent)
    "about",  # the question is ABOUT the file
    "compare",  # the request compares the file with the document
    "source",  # the file is source material for a change to the document
]


class Understanding(BaseModel):
    """What the user is asking for, resolved against this document and conversation.

    It never contains an operation — that is plan_ops/draft's job — which is what makes
    an unresolved request structurally incapable of editing anything.
    """

    intent: Intent
    # One sentence, second person, always naming targets by number. Not shown to the
    # user; it exists to force the model to resolve "it" into an explicit claim number
    # before the next field is written.
    restatement: str

    target_kind: TargetKind
    claim_numbers: list[int]  # [] unless target_kind == "claims"; numbering as shown
    section_heading: str | None = None
    prior_art_role: PriorArtRole

    resolved: bool  # false => a clarifying question is required
    confidence: Literal["high", "medium", "low"]
    question: str | None = None  # required when resolved is false
    options: list[str]  # 0-4 complete instructions the user can click

    # Terminal-outcome marker, set only by `resolve_outcome`. Strict Structured Outputs
    # makes the model emit every field, so whatever it says here is overwritten.
    clarify_exhausted: bool = False


class JudgeVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    failures: list[str]  # one sentence per defect, empty when verdict == "pass"
    suggestion: str  # concrete guidance for the rewrite; "" when verdict == "pass"


def judge_failed(v: JudgeVerdict) -> bool:
    """A "fail" with no stated failures is treated as a pass: the retry would have
    nothing to act on and would burn a call for nothing."""
    return v.verdict == "fail" and bool(v.failures)


class Retrieved(BaseModel):
    """The context a generative or answering node is shown. Internal, never a
    `response_format`."""

    claim_numbers: list[int] = Field(default_factory=list)
    claims_text: str = ""
    # The full body of the one non-claim section `understand` resolved to, when
    # target_kind == "section". "" otherwise.
    section_text: str = ""
    # The document's whole non-claim text, for the EDITING branch. Distinct from
    # `section_text`, which is one resolved section: this is everything, and it is what
    # lets `draft` write in the document's own vocabulary and quote a `find` string that
    # actually matches. "" on the answer branch, whose `claims_text` is already the whole
    # document, and "" for a document with no specification at all.
    spec_text: str = ""
    # Labels of the specification sections `build_spec` could not fit, when it could not
    # fit them. Separate from `omitted_sections` because the two describe different
    # views: that one is what the ANSWER branch could not read, this one is what the
    # EDITING branch could not read, and a request can only ever produce one of them.
    spec_omitted: list[str] = Field(default_factory=list)
    outline: str = ""
    prior_art_excerpt: str = ""  # sanitised but NOT fenced — that happens once, in prompts.py
    # Labels of the sections the Q&A branch could not fit. Carried here because only the
    # node that built the context knows what it left out.
    omitted_sections: list[str] = Field(default_factory=list)
    # False when nothing in the question matched the document's wording, so what the
    # model saw was chosen by position rather than relevance.
    context_matched: bool = True
    # False when the document has no headings, which makes "ask about a section by name"
    # unfollowable advice.
    context_headed: bool = True


class Citation(BaseModel):
    kind: Literal["claim", "section", "prior_art"]
    ref: str  # "3" for a claim, a heading for a section, "uploaded file" for prior art
    quote: str  # a short verbatim span the answer rests on


class Answer(BaseModel):
    text: str
    citations: list[Citation]


GENERATIVE_KINDS: frozenset[str] = frozenset({"insert_claim", "replace_claim", "insert_section"})
"""The three operations that write new prose into a legal document. The others only
rearrange or mark text the user already wrote.

This does not decide whether the user is prompted — sticky per-version consent does. It
tells the confirmation card whether the plan authors new text, which is the single most
useful thing to know before clicking Proceed.
"""


def content_hash(html: str) -> str:
    """Binds a proposal to the document it was computed against: sha256 of the exact
    bytes the client sent, before parsing or sanitising, so both AI routes hash the same
    thing. The only hash function in the AI surface."""
    return hashlib.sha256(html.encode("utf-8")).hexdigest()
