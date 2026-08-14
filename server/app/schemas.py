from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ai.schemas import Op
from app.ai.verify import VerifyReport
from app.models import MAX_TITLE_LENGTH, MAX_VERSION_NAME_LENGTH

# strip_whitespace runs before the length checks, so "   " is a 422 for being
# empty rather than being stored as a name made of spaces. Names are stored in
# exactly the trimmed form validated here.
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH),
]
VersionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_VERSION_NAME_LENGTH),
]


class VersionSummary(BaseModel):
    """A row in the version dropdown."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    name: str
    created_at: datetime
    updated_at: datetime


class DocumentSummary(BaseModel):
    """A row in the patent list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    version_count: int
    updated_at: datetime


class DocumentDetail(BaseModel):
    """Metadata and counts — never content, and never the version list: that is
    its own paginated endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    version_count: int
    latest_version_number: int
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    """`total` is the unfiltered count, so the client can render page counts.

    Spelled out per collection rather than as a generic Page[T]: a pydantic
    generic serialises into OpenAPI as `Page_DocumentSummary_`, which is not a
    name anyone would hand-write on the client.
    """

    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class VersionPage(BaseModel):
    items: list[VersionSummary]
    total: int
    limit: int
    offset: int


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: int
    version_number: int
    name: str
    content: str
    created_at: datetime
    updated_at: datetime


class DocumentCreate(BaseModel):
    """`content` omitted means an empty first version — a new patent starts
    blank, and "" is a legitimate document body."""

    title: Title
    content: str | None = None


class DocumentRename(BaseModel):
    title: Title


class VersionCreate(BaseModel):
    """Body for POST. `name` omitted means "Version {number}".

    Empty content is valid: a user may legitimately clear a draft. There is no
    max_length on content either — the size cap is a router check returning 413
    with a sentence, not a 422 carrying a validation-error array.
    """

    content: str
    name: VersionName | None = None


class VersionUpdate(BaseModel):
    """Body for PUT (update in place). Content only: PUT never renames."""

    content: str


class VersionRename(BaseModel):
    """Body for PATCH. Name only: PATCH never touches content."""

    name: VersionName


# --------------------------------------------------------------------- the AI surface
#
# Everything below is Task 2's wire layer. The engine never imports it: `Op` comes the
# other way, and the two structural Protocols (`understand.Turn`, `prompts.Selection`)
# exist so `ChatTurn` and `AiSelection` can satisfy the graph without the graph
# depending on HTTP.


class ChatTurn(BaseModel):
    """The Literal makes "bad role -> 422" automatic; no validator needed."""

    role: Literal["user", "assistant"]
    content: str


class AiSelection(BaseModel):
    """Read-only context. Deliberately carries no ProseMirror positions: nothing on the
    wire can address a range, so no operation can target one. The client derives all four
    fields; the server treats every one as advisory prose and re-validates
    `claim_numbers` against its own parse."""

    text: str
    claim_numbers: list[int]
    whole_claims: bool
    truncated: bool


class AiOperation(Op):
    """The wire name for a planner operation. Empty subclass: the fields are `Op`'s, so
    the two can never drift, but OpenAPI — and therefore `types.ts` — gets a name that
    reads as part of the AI surface rather than `Op`, which nobody can read in a diff."""


class AiVerifyReport(BaseModel):
    """The wire form of `ai.verify.VerifyReport`, which is a plain dataclass because the
    engine has no wire concerns and no pydantic dependency."""

    ok: bool
    errors: list[str]  # non-empty => the edit was blocked
    warnings: list[str]  # shown, never blocking

    @classmethod
    def of(cls, report: VerifyReport) -> "AiVerifyReport":
        return cls(ok=report.ok, errors=report.errors, warnings=report.warnings)


class AiProposal(BaseModel):
    """Round-trips through the client between the two calls.

    The server keeps no copy: there is no proposal store, no session, and therefore
    nothing to expire on a restart. `created_at` + `proposal_ttl_seconds` is the only
    expiry.

    NOT SIGNED. Forging one lets a user apply deterministic operations to their own
    document — which they can already do by typing. There is no multi-tenancy and no auth
    here, so an HMAC would be ceremony with nothing behind it. If auth is ever added,
    this is the line that changes.

    NO `preview_html`, and no HTML field of any kind. The preview the user reads is
    `summary` plus the operations' `text`/`paragraphs`, rendered as text by the client.
    That keeps invariant 3 exact and mechanical: the only HTML-shaped field anywhere in
    the AI surface is the top-level `html`, and only an applied outcome has one.
    """

    proposal_id: str  # uuid4().hex — client-side dedupe and log correlation
    document_id: int  # echoed from the request; the client checks it before applying
    version_number: int  # echoed from the request; same
    base_sha256: str  # sha256 of the exact html the plan was written against
    created_at: datetime  # UTC, tz-aware
    message: str  # the sentence shown above the Apply / Cancel buttons
    summary: list[str]  # one human-readable line per operation
    # True when the plan writes NEW PROSE rather than only rearranging existing text.
    # Information for the card, not the prompt decision — the prompt decision is consent.
    # The card renders a distinct line for it, because "the AI wrote this sentence" is
    # what a patent attorney needs to know before clicking Proceed.
    authors_new_text: bool
    operations: list[AiOperation]


class AiChatRequest(BaseModel):
    document_id: int
    version_number: int
    html: str  # editor.getHTML() — NEVER the stored version content (PLAN §23.9)
    instruction: str
    context_text: str | None = None
    # The uploaded file's NAME. It is context for `understand` only ("the user attached
    # prior_art.txt"); it never reaches the document and never reaches an operation. It
    # IS interpolated into a prompt, so the router caps it exactly like `context_text`.
    context_name: str | None = None
    selection: AiSelection | None = None
    history: list[ChatTurn] = Field(default_factory=list)
    # THE PROMPT DECISION, supplied by the client. True means the user has already
    # approved AI editing of exactly this document AND this version.
    consented: bool = False
    # --- the clarification loop ------------------------------------------------
    pending_question: str | None = None  # the clarifying question we asked last turn
    clarify_count: int = 0  # consecutive clarifications so far; CLAMPED in the router


class AiApplyRequest(BaseModel):
    html: str  # the LIVE editor html at the moment Apply was clicked
    proposal: AiProposal


AiChatStatus = Literal["applied", "proposal", "answer", "no_change", "needs_clarification", "error"]


class AiChatResponse(BaseModel):
    status: AiChatStatus
    message: str
    html: str | None = None
    proposal: AiProposal | None = None
    verification: AiVerifyReport | None = None
    warnings: list[str] = Field(default_factory=list)
    citations: list[int] = Field(default_factory=list)  # claim numbers, Q&A only
    options: list[str] = Field(default_factory=list)  # clickable prefilled instructions

    @model_validator(mode="after")
    def _payload_matches_outcome(self) -> "AiChatResponse":
        """Invariant 3, and the consent rule, expressed once as a property of the type
        instead of as a property of every return statement someone might later add.

        Nulling is asymmetric on purpose. Dropping a payload on a non-positive outcome
        fails CLOSED — the document cannot change — so it is done silently. A positive
        outcome with a MISSING payload fails OPEN (a "proposal" the user cannot act on,
        or an "applied" that changes nothing while the UI says it did), so it raises:
        that is a programming error and must be loud. It surfaces as a 500 with the
        standard sentence, which is correct — the client did nothing wrong.
        """
        if self.status != "applied":
            self.html = None
            self.verification = None
        elif not self.html:
            raise ValueError("status='applied' requires non-empty html")

        if self.status != "proposal":
            self.proposal = None
        elif self.proposal is None:
            raise ValueError("status='proposal' requires a proposal")

        if self.status != "answer":
            self.citations = []

        if self.status != "needs_clarification":
            self.options = []  # options only ever accompany a question

        return self


AiApplyStatus = Literal["applied", "no_change", "error"]


class AiApplyResponse(BaseModel):
    status: AiApplyStatus
    message: str
    html: str | None = None
    verification: AiVerifyReport | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _html_only_when_applied(self) -> "AiApplyResponse":
        if self.status != "applied":
            self.html = None
            self.verification = None
        elif not self.html:
            raise ValueError("status='applied' requires non-empty html")
        return self
