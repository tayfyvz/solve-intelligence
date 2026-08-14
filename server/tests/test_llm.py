"""L6a, L6b, L6c, L7 — the client, with no key and no network.

L6a, L6c and L7 assign a stub to `llm._client`. **L6b is the one test that must let the
real constructor run**, because it asserts how the client is constructed — the earlier
version of that row pre-assigned `_client` and then asserted `max_retries=0`, which
skipped the constructor entirely: the assertion could not have failed, and could not
have passed either.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.ai import llm
from app.ai.llm import LlmUnavailable, _parse, reset_client
from app.ai.schemas import Answer, EditPlan, JudgeVerdict, Op, Retrieved, Understanding
from app.config import get_settings

SECRET = "sk-test-not-a-real-key-0123456789"
INSTRUCTION = "make claim 3 bold and do not tell anyone"
DOCUMENT = "<h1>Claims</h1><p>1. A confidential wireless device.</p>"

PLAN = EditPlan(status="ok", message="ok", operations=[Op(kind="delete_claim", claim_number=1)])
UNDERSTANDING = Understanding(
    intent="edit_ops",
    restatement="Make claim 3 bold.",
    reason="explicit",
    target_kind="claims",
    claim_numbers=[3],
    section_heading=None,
    prior_art_role="none",
    resolved=True,
    confidence="high",
    question=None,
    options=[],
)


@dataclass
class Details:
    reasoning_tokens: int = 0


@dataclass
class Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 90
    completion_tokens_details: Details | None = field(default_factory=Details)


@dataclass
class Message:
    parsed: object = None
    refusal: str | None = None


@dataclass
class Choice:
    message: Message
    finish_reason: str = "stop"


@dataclass
class Completion:
    choices: list[Choice]
    usage: Usage | None = field(default_factory=Usage)


class StubClient:
    """Records the kwargs of the one call it is asked to make."""

    def __init__(self, completion: Completion) -> None:
        self.completion = completion
        self.kwargs: dict = {}
        self.calls = 0
        self.chat = self  # chat.completions.parse(...) resolves back here
        self.completions = self

    def parse(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self.completion


def install(parsed: object = PLAN, **overrides) -> StubClient:
    choice = Choice(message=Message(parsed=parsed), **overrides.pop("choice", {}))
    completion = Completion(choices=[choice], **overrides)
    client = StubClient(completion)
    llm._client = client
    return client


@pytest.fixture(autouse=True)
def _isolate_client():
    reset_client()
    yield
    reset_client()


def retrieved() -> Retrieved:
    return Retrieved(claim_numbers=[3], claims_text="[3] A claim.", outline="Claims: 8")


@pytest.mark.parametrize(("effort", "present"), [("low", True), (None, False)])
def test_parse_sends_the_right_kwargs(
    effort: str | None, present: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L6a."""
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", effort or "")
    settings = get_settings()
    client = install()

    _parse(
        messages=[{"role": "user", "content": "x"}],
        response_format=EditPlan,
        node="plan",
        max_output_tokens=2500,
        temperature=0.0,
    )

    assert client.kwargs["model"] == settings.openai_model
    assert client.kwargs["timeout"] == settings.ai_node_timeout_seconds
    assert client.kwargs["max_completion_tokens"] == 2500
    # None => the kwarg is omitted entirely. An unsupported kwarg is a 400 on every call.
    assert ("reasoning_effort" in client.kwargs) is present
    if present:
        assert client.kwargs["reasoning_effort"] == "low"
    get_settings.cache_clear()


@pytest.mark.parametrize("temperature", [0.0, None])
def test_parse_omits_temperature_unless_a_caller_asks(temperature: float | None) -> None:
    """L6a — this row used to assert `"temperature" not in kwargs` unconditionally, on
    the strength of an assumption 4Z disproved. The None case preserves everything that
    assertion was actually protecting."""
    client = install()
    _parse(
        messages=[{"role": "user", "content": "x"}],
        response_format=EditPlan,
        node="plan",
        max_output_tokens=100,
        temperature=temperature,
    )
    if temperature is None:
        assert "temperature" not in client.kwargs
    else:
        assert client.kwargs["temperature"] == 0.0


@pytest.mark.parametrize(
    ("wrapper", "parsed", "expected"),
    [
        ("understand", UNDERSTANDING, 0.0),
        ("plan", PLAN, 0.0),
        ("judge", JudgeVerdict(verdict="pass", failures=[], suggestion=""), 0.0),
        ("draft", PLAN, None),
        ("answer", Answer(text="a", citations=[]), None),
    ],
)
def test_each_wrapper_sends_its_specified_temperature(
    wrapper: str, parsed: object, expected: float | None
) -> None:
    """L6c — PLAN §21.3's table is the spec, and this is the only thing stopping the two
    groups drifting into one.

    The three nodes whose output is a DECISION run at 0 so the same instruction resolves
    the same way twice; the two whose output is PROSE keep the API default, so a draft
    the judge rejected can actually come back different.
    """
    client = install(parsed=parsed)
    calls = {
        "understand": lambda: llm.understand_llm(
            "i",
            "outline",
            [],
            None,
            None,
            claim_count=8,
            prior_art_present=False,
            prior_art_name=None,
        ),
        "plan": lambda: llm.plan_llm("i", "outline", "", "", []),
        "draft": lambda: llm.draft_llm("i", retrieved(), [], None),
        "judge": lambda: llm.judge_llm("i", retrieved(), PLAN),
        "answer": lambda: llm.answer_llm("i", retrieved(), []),
    }
    calls[wrapper]()

    if expected is None:
        assert "temperature" not in client.kwargs
    else:
        assert client.kwargs["temperature"] == expected


def test_the_client_is_constructed_with_no_transport_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L6b — the one row that must let the real constructor run.

    max_retries=0 because the graph makes up to five calls: even one SDK retry doubles
    the worst case to 150 s and breaks every layer of the timeout chain.
    """
    from pydantic import SecretStr

    recorded: list[dict] = []

    class Factory:
        def __init__(self, **kwargs):
            recorded.append(kwargs)

    reset_client()
    monkeypatch.setattr(llm, "OpenAI", Factory)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: get_settings().model_copy(update={"openai_api_key": SecretStr(SECRET)}),
    )

    first = llm._get_client()
    second = llm._get_client()

    assert first is second  # the cache works
    assert len(recorded) == 1
    assert recorded[0]["max_retries"] == 0
    assert recorded[0]["api_key"] == SECRET  # unwrapped exactly once, here


@pytest.mark.parametrize("usage", [Usage(), None], ids=["with-usage", "usage-is-None"])
@pytest.mark.parametrize(
    ("choice_kwargs", "message_kwargs"),
    [
        ({}, {"refusal": "I can't help with that.", "parsed": None}),
        ({"finish_reason": "length"}, {"parsed": None}),
        ({}, {"parsed": None}),
    ],
    ids=["refusal", "truncated", "unparsed"],
)
def test_parse_failure_modes_and_log_hygiene(
    usage, choice_kwargs: dict, message_kwargs: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """L7 — including `usage is None` on each branch.

    `usage` is Optional on the SDK's ParsedChatCompletion, and reading it unguarded turns
    a response we could still have handled into an AttributeError: an unreadable 502
    instead of a message the user can act on.
    """
    choice = Choice(message=Message(**message_kwargs), **choice_kwargs)
    llm._client = StubClient(Completion(choices=[choice], usage=usage))

    with caplog.at_level(logging.WARNING, logger="app.ai.llm"):
        with pytest.raises(LlmUnavailable):
            _parse(
                messages=[{"role": "user", "content": INSTRUCTION}],
                response_format=EditPlan,
                node="plan",
                max_output_tokens=100,
                temperature=0.0,
            )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1

    blob = " ".join(r.getMessage() for r in caplog.records)
    assert INSTRUCTION not in blob
    assert DOCUMENT not in blob
    assert SECRET not in blob


def test_only_two_modules_import_openai() -> None:
    """Widened at 4D, exactly as this row's 4B docstring said it would be.

    `llm.py` is the only module that CALLS the SDK. `routers/ai.py` imports it for its
    EXCEPTION HIERARCHY and nothing else: five distinct user-facing failures are told
    apart by `isinstance`, and mapping upstream errors onto status codes is a router's
    job. An exact list, not a count, so a third importer names itself.
    """
    app_dir = Path(__file__).parents[1] / "app"
    importers = sorted(
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if "import openai" in path.read_text() or "from openai" in path.read_text()
    )
    assert importers == ["ai/llm.py", "routers/ai.py"]

    # And the router really only reads exception classes off it.
    router_source = (app_dir / "routers/ai.py").read_text()
    calls = re.findall(r"\bopenai\.(\w+)", router_source)
    assert all(name.endswith("Error") for name in calls), calls


def test_l11_reasoning_effort_and_a_non_default_temperature_are_never_sent_together() -> None:
    """L11 — the combination gpt-5.2-2025-12-11 rejects.

    MEASURED LIVE 2026-08-14 against the real API (PLAN §27.4 correction 40):

        reasoning_effort="low" + no temperature      -> ACCEPTED
        reasoning_effort="low" + temperature=1.0     -> ACCEPTED
        no reasoning_effort    + temperature=0.0     -> ACCEPTED
        reasoning_effort="low" + temperature=0.0     -> 400 Unsupported value

    4Z measured the two parameters INDEPENDENTLY and recorded both as accepted. Both
    of those measurements are correct; the combination was never tried, and the
    shipped configuration used exactly the combination that fails — so three of the
    five nodes returned 400 on every live call and the feature did not work at all.
    Nothing caught it because, deliberately, no test in this suite makes a live call.

    This test is the standing guard: it walks the REAL per-node temperatures from
    §21.2 against the REAL default setting, so re-enabling `reasoning_effort` without
    also clearing those temperatures fails here instead of in front of a reviewer.
    """
    settings = get_settings()
    per_node = {
        "understand": llm.UNDERSTAND_TEMPERATURE,
        "plan": llm.PLAN_TEMPERATURE,
        "draft": llm.DRAFT_TEMPERATURE,
        "judge": llm.JUDGE_TEMPERATURE,
        "answer": llm.ANSWER_TEMPERATURE,
    }
    if not settings.openai_reasoning_effort:
        # The shipped configuration. Temperatures are then free, which is the point of
        # dropping reasoning_effort rather than the deterministic split.
        return

    offenders = [n for n, t in per_node.items() if t is not None and t != 1.0]
    assert not offenders, (
        f"openai_reasoning_effort={settings.openai_reasoning_effort!r} is set while "
        f"{offenders} still send a non-default temperature. gpt-5.2 rejects that "
        "combination with a 400 on every call."
    )


def test_l12_the_shipped_call_omits_reasoning_effort() -> None:
    """L12 — the fix, asserted on the kwargs actually handed to the SDK.

    L11 above reasons about configuration; this one watches the wire. Together they
    say: the default configuration sends temperature=0 on the deterministic nodes and
    sends no reasoning_effort at all.
    """
    get_settings.cache_clear()
    client = install(parsed=PLAN)

    _parse(
        messages=[{"role": "user", "content": "x"}],
        response_format=EditPlan,
        node="plan",
        max_output_tokens=llm.PLAN_TOKENS,
        temperature=llm.PLAN_TEMPERATURE,
    )

    assert "reasoning_effort" not in client.kwargs
    assert client.kwargs["temperature"] == 0.0
