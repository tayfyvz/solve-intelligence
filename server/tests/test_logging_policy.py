"""L8-L10 — the logging policy, asserted rather than promised.

**The document being processed is a customer's unpublished patent application. A log
line is a disclosure.** The rule, stated once in the plan and enforced here:

> The document HTML, the user's instruction, the model prompt, the model's raw
> response, the selected text, the uploaded `.txt` contents, and the API key are NEVER
> written to a log, in any form, at any level.

Lengths, counts, kinds, enum values and truncated hashes only. These tests exist because
the rule is otherwise the kind of thing that is true on the day it is written and false
three commits later — `ai.node=understand ... reason=%s` shipped in 4C and logged the
model's own prose about the instruction, which is exactly the failure mode.

The secrets below are chosen to be unmistakable. If one appears in a log record, the
grep that finds it is the same grep an auditor would run.
"""

import logging

import pytest
from fakes import Recorder, fake_bundle, understanding

from app.ai.graph import GraphInput, run_plan
from app.ai.schemas import Answer, EditPlan, Op

# Distinct, greppable, and present in every place §28.2.5 names as forbidden.
DOC_SECRET = "ZZQQ-DOCUMENT-BODY-SECRET"
INSTRUCTION_SECRET = "ZZQQ-INSTRUCTION-SECRET"
PRIOR_ART_SECRET = "ZZQQ-PRIOR-ART-SECRET"
MODEL_PROSE_SECRET = "ZZQQ-MODEL-PROSE-SECRET"

HTML = (
    f"<p>A widget for {DOC_SECRET}.</p>"
    "<h1>Claims</h1>"
    f"<p>1. A system comprising {DOC_SECRET}, wherein the widget rotates.</p>"
    f"<p>2. The system of claim 1, further comprising {DOC_SECRET}.</p>"
)


@pytest.fixture
def dummy_key(ai_settings):
    """A syntactically valid but fake key, so `ai_enabled` is True for L10.

    The same guard `test_ai_routes.py` applies, and for the same reason: without it this
    file passes on a machine with a real key in `.env` and FAILS on a clean clone, where
    the shipped `sk-XXXXXXXX` placeholder makes `/chat` return 503 before it ever reaches
    the graph — so no `ai.request` line is emitted and the assertions have nothing to
    find. A test whose result depends on a developer's local `.env` is testing the
    environment, not the code. Nothing here reaches the network: the runner is overridden.
    """
    ai_settings(openai_api_key="sk-test-not-a-real-key-0123456789")


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    """Everything a handler would emit: the rendered message AND any traceback text."""
    return "\n".join(
        [r.getMessage() for r in caplog.records] + [r.exc_text or "" for r in caplog.records]
    )


def _assert_clean(logged: str) -> None:
    for secret in (DOC_SECRET, INSTRUCTION_SECRET, PRIOR_ART_SECRET, MODEL_PROSE_SECRET):
        assert secret not in logged, f"{secret} reached a log line"


def test_l8_the_edit_path_logs_no_document_instruction_or_model_prose(caplog) -> None:
    """L8 — understand → plan_ops → verify, every node line, nothing leaked.

    The model's `restatement`, `reason` and `message` all carry the secret, because those
    are the fields that quote the text they describe and are therefore the ones a node is
    most tempted to log.
    """
    rec = Recorder()
    bundle = fake_bundle(
        rec=rec,
        understand=understanding(
            restatement=f"Make claim 1 mention {MODEL_PROSE_SECRET}.",
            reason=f"The user said {MODEL_PROSE_SECRET}.",
            prior_art_role="none",
        ),
        plan=EditPlan(
            status="ok",
            message=f"Bolded the claim about {MODEL_PROSE_SECRET}.",
            operations=[Op(kind="format_claim", claim_number=1, mark="bold", enabled=True)],
        ),
    )
    gi = GraphInput(
        html=HTML,
        instruction=f"Make claim 1 bold, {INSTRUCTION_SECRET}",
        prior_art=f"Prior art says {PRIOR_ART_SECRET}.",
        req="reqtest8",
    )

    with caplog.at_level(logging.DEBUG):
        result = run_plan(gi, bundle)

    assert result.status == "edit"
    logged = _logged(caplog)
    _assert_clean(logged)
    # The line was actually emitted — otherwise this test passes on an empty log.
    assert "ai.node=understand req=reqtest8" in logged
    assert "ai.node=plan_ops req=reqtest8" in logged


def test_l9_the_generative_and_answer_paths_log_no_document_or_prose(caplog) -> None:
    """L9 — retrieve/draft/judge and retrieve/answer.

    `retrieve` builds the claim text that `draft` and `judge` read, and `answer` returns
    free prose with quotes lifted verbatim out of the claims. Those are the three nodes
    holding the most document text at the moment they log.
    """
    rec = Recorder()
    draft_plan = EditPlan(
        status="ok",
        message=f"Rewrote it: {MODEL_PROSE_SECRET}",
        operations=[
            Op(
                kind="replace_claim",
                claim_number=2,
                text=f"The system of claim 1, wherein {MODEL_PROSE_SECRET}.",
            )
        ],
    )
    gi = GraphInput(
        html=HTML,
        instruction=f"Tighten claim 2, {INSTRUCTION_SECRET}",
        prior_art=f"Prior art says {PRIOR_ART_SECRET}.",
        req="reqtest9",
    )

    with caplog.at_level(logging.DEBUG):
        generative = run_plan(
            gi,
            fake_bundle(
                rec=rec,
                understand=understanding(intent="generate", claim_numbers=[2]),
                draft=draft_plan,
            ),
        )
        answered = run_plan(
            gi,
            fake_bundle(
                rec=rec,
                understand=understanding(intent="answer", claim_numbers=[2]),
                answer=Answer(text=f"Claim 2 covers {MODEL_PROSE_SECRET}.", citations=[]),
            ),
        )

    assert generative.status == "edit"
    assert answered.status == "answer"
    logged = _logged(caplog)
    _assert_clean(logged)
    for expected in ("ai.node=retrieve", "ai.node=draft", "ai.node=judge", "ai.node=answer"):
        assert expected in logged, f"{expected} never logged — this test would pass vacuously"


def test_l10_the_route_lines_carry_the_req_and_no_payload(
    client, fake_runner, dummy_key, caplog
) -> None:
    """L10 — `ai.request` and `ai.done` at the HTTP boundary, and the `req=` thread.

    The entry line is the one that sees the whole request at once: document, instruction,
    selection and uploaded file, all in one object. It logs six lengths, three flags and
    twelve characters of a digest.
    """
    from app.ai.graph import GraphResult

    fake_runner(
        GraphResult(
            status="no_change",
            message=f"Nothing to change, {MODEL_PROSE_SECRET}",
            operations=[],
            warnings=[],
            citations=[],
            options=[],
        )
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/ai/chat",
            json={
                "document_id": 1,
                "version_number": 1,
                "html": HTML,
                "instruction": f"Do something, {INSTRUCTION_SECRET}",
                "context_text": f"Prior art: {PRIOR_ART_SECRET}",
                "context_name": "prior.txt",
                "consented": True,
                "history": [],
                "clarify_count": 0,
            },
        )

    assert response.status_code == 200
    logged = _logged(caplog)
    _assert_clean(logged)
    assert "ai.request req=" in logged
    assert "ai.done req=" in logged
    # The same id on both lines is the entire point of threading it.
    entry = next(m for m in logged.split("\n") if m.startswith("ai.request req="))
    done = next(m for m in logged.split("\n") if m.startswith("ai.done req="))
    assert entry.split()[1] == done.split()[1]
    # ...and the SAME id reaches the graph, which is the half of §28.4(a)'s
    # "threaded from route to graph" that the two route lines cannot witness: they
    # would agree with each other whether or not GraphInput ever received it.
    assert fake_runner.calls[0].req == entry.split()[1].removeprefix("req=")
    assert fake_runner.calls[0].req != ""
    # Lengths and flags, not content.
    assert f"html_chars={len(HTML)}" in entry
    assert "has_file=True" in entry
