"""The only module in this repository that calls `openai`.

Logging rule, and it is not optional: log the node, the model, token counts and the
finish reason. Never log the document, the instruction, the uploaded file, the prompt,
the raw response or the key — lengths and counts only.

`langsmith` arrives transitively with `langgraph`. Setting LANGSMITH_TRACING or
LANGCHAIN_TRACING_V2 would send every prompt — a customer's unpublished patent — to a
third party. No code path here enables it; see `.env.example`.
"""

from __future__ import annotations

import logging

from openai import OpenAI
from pydantic import BaseModel

from app.ai import prompts
from app.ai.schemas import (
    Answer,
    EditPlan,
    JudgeVerdict,
    LlmUnavailable,
    Retrieved,
    Understanding,
)
from app.ai.understand import Turn
from app.config import get_settings

logger = logging.getLogger(__name__)

# The three nodes whose output is a DECISION run at temperature 0, so the same
# instruction resolves the same way twice. The two whose output is PROSE keep the API
# default, so a draft the judge rejected can actually come back different.
UNDERSTAND_TOKENS, UNDERSTAND_TEMPERATURE = 1200, 0.0
PLAN_TOKENS, PLAN_TEMPERATURE = 2500, 0.0
DRAFT_TOKENS, DRAFT_TEMPERATURE = 3000, None
JUDGE_TOKENS, JUDGE_TEMPERATURE = 2000, 0.0
ANSWER_TOKENS, ANSWER_TEMPERATURE = 2000, None

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Lazy and cached.

    `OpenAI(api_key=None)` raises when OPENAI_API_KEY is unset, so a module-level client
    would stop the app starting with no key — taking down the 503 path that exists to
    explain the problem.

    max_retries=0: the graph makes up to five calls, so even one SDK retry doubles the
    worst case and breaks the timeout chain. Retry is the judge loop's job.
    """
    global _client
    if _client is None:
        key = get_settings().openai_api_key
        _client = OpenAI(api_key=key.get_secret_value() if key else None, max_retries=0)
    return _client


def reset_client() -> None:
    """The only mutator of module state. For tests that vary settings."""
    global _client
    _client = None


def _parse[T: BaseModel](
    *,
    messages: list[dict[str, str]],
    response_format: type[T],
    node: str,
    max_output_tokens: int,
    temperature: float | None,
) -> T:
    """Every node call funnels through here, so timeout, model, refusal handling and
    logging exist exactly once."""
    settings = get_settings()
    extra: dict[str, object] = {}
    if temperature is not None:
        extra["temperature"] = temperature

    completion = _get_client().chat.completions.parse(
        model=settings.openai_model,
        messages=messages,
        response_format=response_format,
        timeout=settings.ai_node_timeout_seconds,  # per call, not per request
        max_completion_tokens=max_output_tokens,
        **extra,
    )
    choice = completion.choices[0]

    # `usage` is Optional on the SDK's ParsedChatCompletion. Reading it unguarded turns a
    # response we could still have handled into an AttributeError.
    usage = completion.usage
    logger.info(
        "ai.node=%s model=%s in_tokens=%s out_tokens=%s ceiling=%d finish=%s",
        node,
        settings.openai_model,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        max_output_tokens,
        choice.finish_reason,
    )

    if choice.message.refusal:
        logger.warning("ai.node=%s refused len=%d", node, len(choice.message.refusal))
        raise LlmUnavailable(choice.message.refusal)
    if choice.finish_reason == "length":
        logger.warning("ai.node=%s truncated at %d tokens", node, max_output_tokens)
        raise LlmUnavailable("The AI's response was cut off. Try a shorter instruction.")
    if choice.message.parsed is None:
        logger.warning("ai.node=%s parsed is None (finish=%s)", node, choice.finish_reason)
        raise LlmUnavailable("The AI returned a response this app could not read.")
    return choice.message.parsed


# Each wrapper is thin: build messages, call _parse, return the model. No branching and
# no retries of their own — the graph owns the judge retry.


def understand_llm(
    instruction: str,
    outline: str,
    history: list[Turn],
    selection: prompts.Selection | None,
    pending_question: str | None,
    *,
    claim_count: int,
    prior_art_present: bool,
    prior_art_name: str | None,
) -> Understanding:
    """Takes `prior_art_present` and `prior_art_name`, never the file's text. That
    signature is what enforces the anti-injection property: the node that chooses the
    branch has never read the uploaded file."""
    return _parse(
        messages=prompts.build_understand_messages(
            instruction,
            outline,
            history,
            selection,
            pending_question,
            claim_count=claim_count,
            prior_art_present=prior_art_present,
            prior_art_name=prior_art_name,
        ),
        response_format=Understanding,
        node="understand",
        max_output_tokens=UNDERSTAND_TOKENS,
        temperature=UNDERSTAND_TEMPERATURE,
    )


def plan_llm(
    instruction: str,
    outline: str,
    claims: str,
    spec: str,
    prior_art: str,
    history: list[Turn],
    selection: prompts.Selection | None = None,
) -> EditPlan:
    return _parse(
        messages=prompts.build_plan_messages(
            instruction, outline, claims, spec, prior_art, history, selection
        ),
        response_format=EditPlan,
        node="plan",
        max_output_tokens=PLAN_TOKENS,
        temperature=PLAN_TEMPERATURE,
    )


def draft_llm(
    instruction: str,
    retrieved: Retrieved,
    history: list[Turn],
    critique: str | None,
    selection: prompts.Selection | None = None,
) -> EditPlan:
    """Returns an EditPlan rather than free prose, so the generative path lands in the
    same apply engine as the deterministic one."""
    return _parse(
        messages=prompts.build_draft_messages(instruction, retrieved, history, critique, selection),
        response_format=EditPlan,
        node="draft",
        max_output_tokens=DRAFT_TOKENS,
        temperature=DRAFT_TEMPERATURE,
    )


def judge_llm(instruction: str, retrieved: Retrieved, plan: EditPlan) -> JudgeVerdict:
    return _parse(
        messages=prompts.build_judge_messages(instruction, retrieved, plan),
        response_format=JudgeVerdict,
        node="judge",
        max_output_tokens=JUDGE_TOKENS,
        temperature=JUDGE_TEMPERATURE,
    )


def answer_llm(instruction: str, retrieved: Retrieved, history: list[Turn]) -> Answer:
    return _parse(
        messages=prompts.build_answer_messages(instruction, retrieved, history),
        response_format=Answer,
        node="answer",
        max_output_tokens=ANSWER_TOKENS,
        temperature=ANSWER_TEMPERATURE,
    )
