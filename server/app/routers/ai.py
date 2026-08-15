"""The two AI routes: `POST /api/ai/chat` (run 1) and `POST /api/ai/apply` (run 2).

Four properties, each visible in one line rather than maintained by discipline:

1. **Neither handler takes a `db` parameter**, so no code path through either route can
   reach the database.
2. **`html` is non-null iff the document changed**, enforced by a `model_validator` on
   the response models rather than by every `return` statement.
3. **The first AI change on a version cannot land without a second user action**, decided
   in Python from one boolean the client sent — never from operation kinds, and never
   from anything the model said about itself.
4. **`/apply` never calls OpenAI** and works with no key configured.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

# Imported for its EXCEPTION CLASSES only — nothing here calls the SDK. The rule that
# keeps `openai` out of the engine is about `app/ai/`; a router that maps upstream errors
# onto status codes is exactly where the SDK's exception hierarchy belongs.
import openai
from fastapi import APIRouter, Depends, HTTPException, status

from app.ai.apply import apply_plan
from app.ai.document import parse, render
from app.ai.graph import AiRunner, GraphInput, GraphResult, get_ai_runner
from app.ai.schemas import GENERATIVE_KINDS, Op, PlanError, content_hash, require
from app.ai.summary import summarise
from app.ai.understand import MAX_CLARIFY_TURNS, clarify_floor
from app.ai.verify import verify
from app.config import Settings, get_settings
from app.sanitize import sanitize_html
from app.schemas import (
    AiApplyRequest,
    AiApplyResponse,
    AiChatRequest,
    AiChatResponse,
    AiOperation,
    AiProposal,
    AiVerifyReport,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

# Every sentence a user can see lives here, so the tests import them rather than retyping
# them: a test that retypes copy only asserts that someone typed it twice.

HTML_TOO_LARGE = "This document is too large for AI editing (limit 200,000 characters)."
CONTEXT_TOO_LARGE = "The uploaded file is too large (limit 40,000 characters)."
CONTEXT_NAME_TOO_LONG = "That file name is too long (limit 120 characters)."
SELECTION_TOO_LARGE = (
    "That selection is too large to send to the AI (limit 8,000 characters). "
    "Please select less of the document."
)
EMPTY_INSTRUCTION = "Please type an instruction for the AI."
INSTRUCTION_TOO_LONG = "That instruction is too long (limit 2,000 characters)."
NO_OPERATIONS = "This suggestion contains no changes. Please ask again."
TOO_MANY_TO_APPLY = (
    "This suggestion contains too many changes (limit 20). Please ask for a smaller change."
)
STALE_PROPOSAL = (
    "The document changed after this suggestion was written, so it was not applied. "
    "Please ask the AI again."
)
EXPIRED_PROPOSAL = (
    "This suggestion has expired. Please ask the AI again so it can see your current text."
)
NO_KEY_MESSAGE = "AI editing is unavailable because no OpenAI API key is configured."
BUSY_MESSAGE = "The AI service is busy right now. Please try again in a moment."
KEY_REJECTED_MESSAGE = "The configured OpenAI API key was rejected."
MODEL_MISSING_MESSAGE = "The configured AI model is not available. Check OPENAI_MODEL."
UPSTREAM_MESSAGE = "The AI service returned an error. Your document was not changed."
TIMEOUT_MESSAGE = "The AI took too long to respond. Your document was not changed."

RESULT_TOO_LARGE = (
    "That change would make the document too large (limit 200,000 characters). "
    "Please ask for a smaller change."
)

NOTHING_TO_CHANGE = "I couldn't find anything to change."
NO_VISIBLE_CHANGE = "Applying that produced no change to the document."
INVALID_SUGGESTION = "This suggestion is no longer valid: {reason}"
# A plan this big is a misunderstanding, not a task, and every extra operation multiplies
# the blast radius of one wrong claim number. The sentence says what to do next.
TOO_MANY_OPERATIONS = (
    "That instruction needs too many changes (limit 20). "
    "Please ask for a smaller change — one claim at a time works well."
)

# APITimeoutError subclasses APIConnectionError, and AuthenticationError,
# PermissionDeniedError, NotFoundError and RateLimitError all subclass APIStatusError, so
# the wrong order silently collapses five distinct user messages into one. This tuple's
# order IS the specificity order — do not sort it alphabetically.
_OPENAI_STATUS: tuple[tuple[type[Exception], int, str], ...] = (
    (asyncio.TimeoutError, 504, TIMEOUT_MESSAGE),
    (openai.APITimeoutError, 504, TIMEOUT_MESSAGE),
    (openai.RateLimitError, 429, BUSY_MESSAGE),
    (openai.AuthenticationError, 502, KEY_REJECTED_MESSAGE),
    (openai.PermissionDeniedError, 502, KEY_REJECTED_MESSAGE),
    (openai.NotFoundError, 502, MODEL_MISSING_MESSAGE),
    (openai.APIStatusError, 502, UPSTREAM_MESSAGE),
    (openai.APIConnectionError, 502, UPSTREAM_MESSAGE),
    # Last, and it catches the constructor failure: `OpenAI(api_key=None)` raises. The
    # ai_enabled gate should make that unreachable, but catching it turns a 500 into a
    # 502 with a sentence.
    (openai.OpenAIError, 502, UPSTREAM_MESSAGE),
)


# How far into the future a proposal's `created_at` may sit before it is treated as
# expired. The client stamps it from its own clock, so a small skew is normal.
CLOCK_SKEW_SECONDS = 60


def _cap_or_413(value: str, cap: int, message: str) -> None:
    """Every size cap on this surface is a 413 with a sentence. Deliberately not a
    Pydantic `max_length`, which produces a 422 whose detail is a validation-error array
    — neither the right status nor a message a user could read."""
    if len(value) > cap:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, message)


# AI caps are measured in CHARACTERS; the save path's cap is measured in UTF-8 BYTES.
# Deliberate: the save cap protects the database and the wire, the AI caps protect a
# token budget. Do not "harmonise" them.


def _apply_and_verify(
    html: str, operations: list[Op], settings: Settings
) -> tuple[str, str, str | None, AiVerifyReport | None, list[str]]:
    """Returns `(status, message, html_or_None, report_or_None, warnings)`.

    The only place either route turns operations into HTML, which is why the two cannot
    diverge. The caller substitutes its own `message` on the positive paths.
    """
    result = apply_plan(html, operations)  # 1. pure engine; verifies and require()s
    if result.html is None:  # 2. the plan was refused, or verify
        return ("error", result.report.errors[0], None, None, result.warnings)

    # 2b. The input cap bounds `body.html` and nothing else: twenty operations carrying
    #     95,000 characters each produced ~10x the AI cap and ~2x the save cap, so the
    #     user was shown an edit they could never save. Same cap, same unit, on the way out.
    if len(result.html) > settings.max_html_chars:
        return ("error", RESULT_TOO_LARGE, None, None, result.warnings)

    # 3. Did the OPERATIONS change anything? Compared in ONE space, the engine's own
    #    canonical form: `result.html` is canonical by construction but the client's bytes
    #    may not be, so a direct comparison reports a canonicalisation as an edit.
    if result.html == render(parse(html)):
        return ("no_change", NO_VISIBLE_CHANGE, None, None, result.warnings)

    out = sanitize_html(result.html)  # 4. nh3 can change bytes
    # 5. Re-verify the bytes that SHIP — what is checked here is that nh3 did not break
    #    canonical form. `expected_claims=None`, because the applier's own count was
    #    already checked in step 1. `deleted_numbers` IS passed: it is a fact about the
    #    plan, not about the applier's belief, and dropping it re-introduces the
    #    self-reference warning verify.py suppresses on purpose.
    report = verify(html, out, expected_claims=None, deleted_numbers=result.deleted_numbers)
    if report.errors:
        return ("error", report.errors[0], None, None, result.warnings)
    return (
        "applied",
        "",
        out,
        AiVerifyReport.of(report),
        list(dict.fromkeys(result.warnings + report.warnings)),
    )


def _upstream_error(exc: Exception, instruction_chars: int, req: str) -> HTTPException:
    """First match wins, so the tuple's order is the specificity order."""
    for kind, code, message in _OPENAI_STATUS:
        if isinstance(exc, kind):
            # The exception TYPE and the status, never the body: provider error bodies
            # echo the prompt back, and the prompt is the customer's patent. The
            # instruction's LENGTH, never the instruction.
            logger.warning(
                "ai.llm_error req=%s node=- type=%s status=%d instruction_chars=%d",
                req,
                type(exc).__name__,
                code,
                instruction_chars,
            )
            return HTTPException(code, message)
    raise exc  # not ours — the middleware in main.py turns it into a 500


def _done(req: str, started: float, llm_calls: int, response: AiChatResponse) -> AiChatResponse:
    """The terminal line, on every path out of /chat that reached the graph. Wrapped
    around the return rather than repeated at nine call sites, so a future branch cannot
    silently lose the only record that the request finished. `message` is never logged:
    it is model prose."""
    logger.info(
        "ai.done req=%s status=%s ms_total=%d llm_calls=%d warnings=%d html=%s proposal=%s",
        req,
        response.status,
        (time.monotonic() - started) * 1000,
        llm_calls,
        len(response.warnings),
        response.html is not None,
        # The correlation key between this /chat and the /apply that may follow it.
        # /apply is handed a proposal and nothing else, so the id the proposal already
        # carries does the correlating and no field is added to the wire for a log line.
        response.proposal.proposal_id if response.proposal else "-",
    )
    return response


@router.post("/chat", response_model=AiChatResponse)
async def ai_chat(
    body: AiChatRequest,
    settings: Settings = Depends(get_settings),
    runner: AiRunner = Depends(get_ai_runner),
) -> AiChatResponse:
    """Run 1. Never writes to the database — there is no `db` parameter to write with."""
    _cap_or_413(body.html, settings.max_html_chars, HTML_TOO_LARGE)  # 1
    _cap_or_413(body.context_text or "", settings.max_context_chars, CONTEXT_TOO_LARGE)  # 2
    # 2b
    _cap_or_413(body.context_name or "", settings.max_context_name_chars, CONTEXT_NAME_TOO_LONG)
    selection_text = body.selection.text if body.selection else ""
    _cap_or_413(selection_text, settings.max_selection_chars, SELECTION_TOO_LARGE)  # 3

    instruction = body.instruction.strip()  # 4
    if not instruction:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, EMPTY_INSTRUCTION)
    # The STRIPPED value on both checks: measuring emptiness after the strip and length
    # before it rejects 1,995 real characters followed by a newline as "too long".
    if len(instruction) > settings.max_instruction_chars:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, INSTRUCTION_TOO_LONG)

    history = body.history[-settings.max_history_turns * 2 :]  # 5. truncate, never reject

    # 6. BEFORE any work. Parsing a 200,000-character document only to return 503 is work
    #    nobody asked for, and the no-key state is the reviewer's most likely one.
    if not settings.ai_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_KEY_MESSAGE)

    base = content_hash(body.html)  # 7. the digest of the exact request bytes

    # One UUID per request, threaded into GraphInput and logged by every node. Not
    # tracing: no spans, no propagation to the OpenAI call, no sampling, no backend.
    req = uuid4().hex
    started = time.monotonic()
    # Lengths, counts, flags and a truncated digest. Not one character of the document,
    # the instruction, the selection or the uploaded file.
    logger.info(
        "ai.request req=%s doc=%s ver=%d html_chars=%d html_sha=%s instr_chars=%d "
        "has_selection=%s has_file=%s file_chars=%d consented=%s clarify_count=%d",
        req,
        body.document_id,
        body.version_number,
        len(body.html),
        base[:12],
        len(instruction),
        body.selection is not None,
        bool(body.context_text),
        len(body.context_text or ""),
        body.consented,
        body.clarify_count,
    )

    # `clarify_count` is untrusted, and the clamp alone is worthless: it bounds the upper
    # end, but 0 is the lower end and the client supplies it, so a client sending 0 every
    # turn would get an unbounded sequence of questions. `clarify_floor` re-derives the
    # count from the transcript, which is the same evidence the model is given — so a
    # client that strips its own questions to lower the floor also destroys the context
    # that makes its conversation work.
    clamped = min(max(body.clarify_count, clarify_floor(history)), MAX_CLARIFY_TURNS)

    graph_input = GraphInput(
        html=body.html,
        instruction=instruction,
        prior_art=body.context_text or "",
        prior_art_name=body.context_name,
        selection=body.selection,
        history=list(history),
        pending_question=body.pending_question,
        clarify_count=clamped,
        req=req,
    )
    try:
        # 8. `to_thread` because the graph is synchronous and CPU-and-network bound.
        #    `wait_for` bounds the RESPONSE, not the work: cancelling abandons the result,
        #    but the worker thread is not cancellable and the OpenAI call still bills in
        #    full. The graph is pure and holds no database handle, so an abandoned thread
        #    can corrupt nothing — the exposure is cost, not data.
        result: GraphResult = await asyncio.wait_for(
            asyncio.to_thread(runner, graph_input), settings.ai_request_timeout_seconds
        )
    except Exception as exc:
        raise _upstream_error(exc, len(instruction), req) from exc

    if result.status == "clarification":  # 9
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(
                status="needs_clarification",
                message=result.message,
                options=result.options,
                warnings=result.warnings,
            ),
        )
    if result.status == "answer":  # 10
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(
                status="answer",
                message=result.message,
                citations=result.citations,
                warnings=result.warnings,
            ),
        )
    if result.status == "error":  # 11
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(status="error", message=result.message, warnings=result.warnings),
        )
    if result.status == "no_change":
        # 11b. The graph's own terminal outcome, and it keeps its own sentence: the
        # clarify budget is spent, so the graph has stopped asking and said what it can do
        # instead. Falling through to step 12 would replace that with "I couldn't find
        # anything to change", which is not what happened. Both outcomes carry zero
        # operations, so ordering is the only thing that tells them apart.
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(status="no_change", message=result.message, warnings=result.warnings),
        )
    if not result.operations:  # 12 — an "edit" the planner emptied
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(status="no_change", message=NOTHING_TO_CHANGE, warnings=result.warnings),
        )
    if len(result.operations) > settings.max_operations:  # 13
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(status="error", message=TOO_MANY_OPERATIONS, warnings=result.warnings),
        )
    try:  # 14
        for op in result.operations:
            require(op)
    except PlanError as exc:
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(
                status="error",
                message=INVALID_SUGGESTION.format(reason=exc),
                warnings=result.warnings,
            ),
        )

    # 15. THE PROMPT DECISION. One boolean, supplied by the client, read in Python — not
    # the operation kinds, not the intent, and not anything the model said about itself.
    # Consent is sticky per version: the first AI change on a version is confirmed and
    # creates a restore point, and every change after that on the same version is
    # ordinary editing. The client derives the boolean from {documentId, versionNumber}
    # against the live store, so a forgotten call site fails CLOSED.
    #
    # It returns BEFORE `_apply_and_verify` is ever called, so on an unconsented version
    # the server never produces edited HTML at all — `html` is structurally absent here
    # rather than merely withheld.
    if not body.consented:
        return _done(
            req,
            started,
            result.llm_calls,
            AiChatResponse(
                status="proposal",
                message=result.message,
                proposal=_build_proposal(body, base, result),
                warnings=result.warnings,
            ),
        )

    outcome, message, html, report, warnings = _apply_and_verify(  # 16-20
        body.html, list(result.operations), settings
    )
    return _done(
        req,
        started,
        result.llm_calls,
        AiChatResponse(
            status=outcome,
            # The plan's own sentence is what the user reads on success; the engine only
            # supplies one when it knows something the plan does not.
            message=result.message if outcome == "applied" else message,
            html=html,
            verification=report,
            warnings=list(dict.fromkeys(result.warnings + warnings)),
        ),
    )


def _build_proposal(body: AiChatRequest, base: str, result: GraphResult) -> AiProposal:
    return AiProposal(
        proposal_id=uuid4().hex,
        document_id=body.document_id,
        version_number=body.version_number,
        base_sha256=base,
        created_at=datetime.now(UTC),
        message=result.message,
        summary=[summarise(op) for op in result.operations],
        # Information for the card, never a branch that decides whether to apply.
        authors_new_text=any(op.kind in GENERATIVE_KINDS for op in result.operations),
        operations=[AiOperation.model_validate(op.model_dump()) for op in result.operations],
    )


@router.post("/apply", response_model=AiApplyResponse)
def ai_apply(
    body: AiApplyRequest,
    settings: Settings = Depends(get_settings),
) -> AiApplyResponse:
    """Run 2. Three things are absent from this signature and all three are load-bearing:
    no `db`, so nothing here can write; no `runner`, so this route never reaches OpenAI
    and works with no key configured; and no `async`, because it is CPU-bound and belongs
    in the threadpool FastAPI already gives a `def` handler.
    """
    started = time.monotonic()
    proposal = body.proposal
    _cap_or_413(body.html, settings.max_html_chars, HTML_TOO_LARGE)  # 1
    if not proposal.operations:  # 2
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, NO_OPERATIONS)
    if len(proposal.operations) > settings.max_operations:  # 3
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, TOO_MANY_TO_APPLY)

    # 4 before 5: an expired proposal and a stale one are both 409 but say different
    # things, and a proposal that is both should get the more actionable message.
    created = proposal.created_at
    if created.tzinfo is None:  # a client that dropped the offset must not become a 500
        created = created.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - created).total_seconds()
    # Both directions: a one-sided check makes `created_at: "2999-01-01"` a proposal that
    # never expires, which is the one property the TTL exists to deny. The allowance is
    # for a clock that runs slightly fast, not for a client that invents a date.
    if age > settings.proposal_ttl_seconds or age < -CLOCK_SKEW_SECONDS:
        raise HTTPException(status.HTTP_409_CONFLICT, EXPIRED_PROPOSAL)
    if content_hash(body.html) != proposal.base_sha256:  # 5
        raise HTTPException(status.HTTP_409_CONFLICT, STALE_PROPOSAL)

    try:  # 6. the proposal came back through an untrusted client: re-validate from scratch
        for op in proposal.operations:
            require(op)
    except PlanError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, INVALID_SUGGESTION.format(reason=exc)
        ) from exc

    outcome, message, html, report, warnings = _apply_and_verify(  # 7-11
        body.html, list(proposal.operations), settings
    )
    # After the guards rather than at entry: every guard above raises, so a line here
    # means "this proposal was applied" and an /apply with no line was refused.
    logger.info(
        "ai.apply proposal=%s ops=%d verify_ok=%s ms=%d",
        proposal.proposal_id,
        len(proposal.operations),
        outcome == "applied",
        (time.monotonic() - started) * 1000,
    )
    return AiApplyResponse(
        status=outcome,
        message=proposal.message if outcome == "applied" else message,
        html=html,
        verification=report,
        warnings=warnings,
    )
