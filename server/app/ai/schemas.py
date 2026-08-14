"""Contracts between the language model and the deterministic engine.

**No field constraints on a planner-facing model.** `Field(ge=…)`, `min_length`,
`pattern` and friends make `to_strict_json_schema` emit `"minimum": 1` and its
relatives; whether strict Structured Outputs rejects those is unverifiable without a
key, so avoiding them is a free mitigation under an assumption (C3) — and it is labelled
as an assumption, not a verified fact. **Every bound is enforced in Python after
parsing**, by `require()`. P2 enforces the split mechanically so nobody has to remember
it.

Planner-facing (passed as `response_format`): EditPlan, Op, Understanding, JudgeVerdict,
Answer, Citation. Internal (never a response_format, so constraints are welcome):
Retrieved, Proposal.

This module must never import `openai` or `langgraph` (invariant 1, test T5).
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

OpKind = Literal[
    "format_claim",
    "delete_claim",
    "insert_claim",
    "replace_claim",
    "insert_section",
    "replace_text",
]  # six — delete_section was cut (PLAN §1.1)


class Op(BaseModel):
    """One flat model with optional fields, NOT a discriminated union.

    Strict Structured Outputs supports anyOf, but a flat model produces one $def and one
    obvious schema, and per-kind validation has to happen in Python anyway (REQUIRED /
    require below). At ~10 kinds this becomes validation soup and the migration is a
    union keyed on `kind`; naming the seam is worth more than pre-building it.
    """

    kind: OpKind
    claim_number: int | None = None
    after_claim_number: int | None = None  # 0 = before claim 1
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


REQUIRED: dict[str, tuple[str, ...]] = {
    "format_claim": ("claim_number", "mark", "enabled"),
    "delete_claim": ("claim_number",),
    "insert_claim": ("after_claim_number", "text"),
    "replace_claim": ("claim_number", "text"),
    "insert_section": ("heading", "paragraphs", "position"),
    "replace_text": ("find", "replace"),
}

MAX_CLAIM_NUMBER = 999
MAX_SECTION_PARAGRAPHS = 20


def require(op: Op) -> None:
    """Per-kind validation. Bounds live here, not in the schema.

    This IS the pre-apply validation of an operation. There is no `verify_plan`; a second
    pre-apply validator would be a second place to keep in sync.
    """
    # A bare subscript on purpose: op.kind is a Pydantic-validated Literal, so a KeyError
    # is impossible by construction, and .get() would only hide a kind added to OpKind
    # without a REQUIRED row. P3 turns that omission into a test failure instead.
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

    This model NEVER contains an operation — that is plan_ops/draft's job — which is what
    makes an unresolved request structurally incapable of editing anything. Routing is a
    projection of understanding, not a separate question: `intent` is one field of twelve.
    """

    # --- what they want ------------------------------------------------------
    intent: Intent
    restatement: str  # one sentence, second person, ALWAYS naming targets by number
    reason: str  # one short clause, logged, never shown

    # --- what it applies to --------------------------------------------------
    target_kind: TargetKind
    claim_numbers: list[int]  # [] unless target_kind == "claims"; numbering as shown
    section_heading: str | None = None
    prior_art_role: PriorArtRole

    # --- how sure it is ------------------------------------------------------
    resolved: bool  # false => a clarifying question is required
    confidence: Literal["high", "medium", "low"]
    question: str | None = None  # required when resolved is false; one sentence, no JSON
    options: list[str]  # 0-4 complete instructions the user can click

    # --- terminal-outcome marker: set ONLY by resolve_outcome, never by the model.
    # Understanding is a Structured Outputs response type, so the model emits this field
    # whether or not the prompt names it; gate_understanding overwrites it on every path.
    clarify_exhausted: bool = False


class JudgeVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    failures: list[str]  # one sentence per defect, empty when verdict == "pass"
    suggestion: str  # concrete guidance for the rewrite; "" when verdict == "pass"


def judge_failed(v: JudgeVerdict) -> bool:
    """A verdict of "fail" with no stated failures is treated as a pass.

    A judge that says "fail" but cannot name a defect gives `draft` nothing to act on, so
    the retry would be identical and would burn a call for nothing.
    """
    return v.verdict == "fail" and bool(v.failures)


class Retrieved(BaseModel):
    """Deterministic selection of the context a generative node needs. Internal, so
    constraints would be permitted here — there just are none worth having."""

    claim_numbers: list[int] = Field(default_factory=list)
    claims_text: str = ""
    outline: str = ""
    prior_art_excerpt: str = ""  # sanitised but NOT yet fenced — that happens once, in prompts.py
    prior_art_truncated: bool = False


class Citation(BaseModel):
    kind: Literal["claim", "section", "prior_art"]
    ref: str  # "3" for a claim, a heading for a section, "uploaded file" for prior art
    quote: str  # a short verbatim span the answer rests on


class Answer(BaseModel):
    text: str
    citations: list[Citation]


GENERATIVE_KINDS: frozenset[str] = frozenset({"insert_claim", "replace_claim", "insert_section"})
"""The three operations that write NEW PROSE into a legal document. The other three
rearrange or mark text the user already wrote and approved.

This does NOT decide whether the user is prompted — sticky per-version consent does. It
survives, demoted, for one job: telling the confirmation card whether the plan authors
new text, which is the single most useful thing to know before clicking Proceed.

Defined ONCE, here. `routers/ai.py` imports it; two module-level frozensets of the same
thing is exactly how they drift.
"""


def authors_new_text(plan: EditPlan) -> bool:
    """True when a plan writes new prose rather than only rearranging existing text.

    Computed in Python from the operation KINDS, never from anything a model says about
    itself. It was renamed when consent became sticky per version: its previous name
    said it decided whether to confirm, which it no longer does, and a function whose
    name lies is worse than no function at all. P6 greps for the old name.
    """
    return any(op.kind in GENERATIVE_KINDS for op in plan.operations)


def content_hash(html: str) -> str:
    """The binding between a proposal and the document it was computed against.

    sha256 of the exact bytes the client sent, computed BEFORE parsing or sanitising, so
    that both AI routes hash the same thing.

    THE ONLY hash function in the AI surface. Two copies of a hash do not diverge loudly;
    they diverge into a 409 on every apply, or into a check that silently stops checking.
    """
    return hashlib.sha256(html.encode("utf-8")).hexdigest()
