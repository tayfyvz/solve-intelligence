"""The ONLY module in this repository that CALLS `openai`.

LangSmith warning: `langsmith` is in the dependency tree as a transitive dependency of
`langgraph`. Setting LANGSMITH_TRACING or LANGCHAIN_TRACING_V2 in any environment sends
every prompt — i.e. the customer's unpublished patent text — to a third party. No code
path here enables it; see server/.env.example.

Logging rules, and they are not optional. Log the node name, model, token counts,
reasoning tokens, ceiling and finish reason. **Never log verbatim** the document HTML,
the instruction, the uploaded prior-art text, the selection, the full prompt or the raw
response — log lengths and hashes instead. Never log the key in any form.
"""

from __future__ import annotations

import logging

from openai import OpenAI
from pydantic import BaseModel

from app.ai import prompts
from app.ai.schemas import Answer, EditPlan, JudgeVerdict, Retrieved, Understanding
from app.ai.understand import Turn
from app.config import get_settings

logger = logging.getLogger(__name__)

# PLAN §21.3. The three nodes whose output is a DECISION run at temperature=0, so the
# same instruction resolves the same way twice; the two whose output is PROSE keep the
# API default, so a draft rejected by the judge can actually come back different.
# 4Z measured 0.0, 1.0 and 2.0 all ACCEPTED on this model, correcting the earlier
# "reasoning model — send no temperature" assumption, which was never tested.
UNDERSTAND_TOKENS, UNDERSTAND_TEMPERATURE = 1200, 0.0
PLAN_TOKENS, PLAN_TEMPERATURE = 2500, 0.0
DRAFT_TOKENS, DRAFT_TEMPERATURE = 3000, None
JUDGE_TOKENS, JUDGE_TEMPERATURE = 2000, 0.0
ANSWER_TOKENS, ANSWER_TEMPERATURE = 2000, None

_client: OpenAI | None = None


class LlmUnavailable(RuntimeError):
    """The model returned nothing usable. Carries a message a user could read."""


def _get_client() -> OpenAI:
    """Lazy, cached.

    `OpenAI(api_key=None)` raises `OpenAIError` when OPENAI_API_KEY is also unset, so a
    module-level client makes the whole app fail to start with no key — taking down the
    503 path and the entire no-key reviewer experience with it. Construct on first use.

    max_retries=0, not the SDK default of 2 and not 1: the graph makes up to FIVE calls,
    so even one SDK retry doubles the worst case to 150 s and breaks the timeout chain in
    PLAN §3.4. Retry is the judge loop's job — the retry the user actually benefits from.
    The accepted cost is that one transient 429 fails the request.
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
    # None => omit the kwarg entirely. An unsupported kwarg is a 400, and a 400 on every
    # call is not a configuration mistake to make in front of a reviewer.
    if settings.openai_reasoning_effort:
        extra["reasoning_effort"] = settings.openai_reasoning_effort
    if temperature is not None:
        extra["temperature"] = temperature

    completion = _get_client().chat.completions.parse(
        model=settings.openai_model,
        messages=messages,
        response_format=response_format,
        timeout=settings.ai_node_timeout_seconds,  # PER CALL — PLAN §3.4
        max_completion_tokens=max_output_tokens,  # 4Z measured reasoning_tokens == 0,
        **extra,  # so this bounds the VISIBLE answer
    )
    choice = completion.choices[0]

    # `usage` is Optional on the SDK's ParsedChatCompletion. Reading it unguarded turns a
    # response we could still have handled — a refusal, a truncation — into an
    # AttributeError, i.e. an unreadable 502 instead of a readable message. Log what is
    # there; never let logging decide the control flow.
    usage = completion.usage
    reasoning = None
    if usage is not None and usage.completion_tokens_details is not None:
        reasoning = usage.completion_tokens_details.reasoning_tokens
    logger.info(
        "ai.node=%s model=%s in_tokens=%s out_tokens=%s reasoning_tokens=%s ceiling=%d finish=%s",
        node,
        settings.openai_model,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        reasoning,
        max_output_tokens,
        choice.finish_reason,
    )

    if choice.message.refusal:
        logger.warning("ai.node=%s refused len=%d", node, len(choice.message.refusal))
        raise LlmUnavailable(choice.message.refusal)
    if choice.finish_reason == "length":
        # 4Z measured 74-129 completion tokens against ceilings of 1200-3000, so this is
        # unreachable on any ordinary input. It stays for the pathological one. The
        # ceiling is logged so the fix is obvious from one line.
        logger.warning(
            "ai.node=%s truncated at %d tokens (reasoning_tokens=%s)",
            node,
            max_output_tokens,
            reasoning,
        )
        raise LlmUnavailable("The AI's response was cut off. Try a shorter instruction.")
    if choice.message.parsed is None:
        logger.warning("ai.node=%s parsed is None (finish=%s)", node, choice.finish_reason)
        raise LlmUnavailable("The AI returned a response this app could not read.")
    return choice.message.parsed


# ---------------------------------------------------------------------- the wrappers
# Each is thin: build messages, call _parse, return the model. No branching and no
# retries of their own — the graph owns the judge retry and there is no transport retry.


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
    """Takes `prior_art_present: bool` and `prior_art_name`, never the file's text.

    That signature IS the enforcement of the anti-injection property described in
    prompts.build_understand_messages. L8 asserts there is no parameter that could carry
    the file.
    """
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
    instruction: str, outline: str, claims: str, prior_art: str, history: list[Turn]
) -> EditPlan:
    return _parse(
        messages=prompts.build_plan_messages(instruction, outline, claims, prior_art, history),
        response_format=EditPlan,
        node="plan",
        max_output_tokens=PLAN_TOKENS,
        temperature=PLAN_TEMPERATURE,
    )


def draft_llm(
    instruction: str, retrieved: Retrieved, history: list[Turn], critique: str | None
) -> EditPlan:
    """Returns an EditPlan rather than free prose, so the generative path lands in the
    same apply engine as the deterministic one. There is one apply pipeline."""
    return _parse(
        messages=prompts.build_draft_messages(instruction, retrieved, history, critique),
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
