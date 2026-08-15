"""The two AI routes.

Every test runs without a real API key and without a network: the graph is replaced
through `dependency_overrides`, so the real HTTP stack — validation, status codes,
response models, middleware — is exercised while nothing reaches OpenAI.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.apply import apply_plan
from app.ai.graph import GraphResult
from app.ai.schemas import Op, content_hash
from app.ai.understand import CAPABILITY_STATEMENT
from app.data import SEED_DOCUMENTS
from app.routers import ai as ai_module
from app.routers.ai import (
    BUSY_MESSAGE,
    CONTEXT_NAME_TOO_LONG,
    CONTEXT_TOO_LARGE,
    EMPTY_INSTRUCTION,
    EXPIRED_PROPOSAL,
    HTML_TOO_LARGE,
    INSTRUCTION_TOO_LONG,
    KEY_REJECTED_MESSAGE,
    MODEL_MISSING_MESSAGE,
    NO_KEY_MESSAGE,
    NO_OPERATIONS,
    NO_VISIBLE_CHANGE,
    NOTHING_TO_CHANGE,
    RESULT_TOO_LARGE,
    SELECTION_TOO_LARGE,
    STALE_PROPOSAL,
    TIMEOUT_MESSAGE,
    TOO_MANY_OPERATIONS,
    TOO_MANY_TO_APPLY,
    UPSTREAM_MESSAGE,
    ai_apply,
    ai_chat,
)
from app.sanitize import sanitize_html
from app.schemas import AiApplyResponse, AiChatResponse, AiOperation, AiProposal

SEED_1 = SEED_DOCUMENTS[0].content  # 8 claims
DUMMY_KEY = "sk-test-not-a-real-key-0123456789"
TWO_CLAIMS = "<h1>Claims</h1><p>1. A widget.</p><p>2. The widget of claim 1.</p>"

# One operation per kind, each of which really changes SEED_1.
OPS: dict[str, Op] = {
    "format_claim": Op(kind="format_claim", claim_number=1, mark="bold", enabled=True),
    "delete_claim": Op(kind="delete_claim", claim_number=3),
    "insert_claim": Op(
        kind="insert_claim",
        after_claim_number=2,
        text="The device of claim 2, wherein the housing comprises borosilicate glass.",
    ),
    "replace_claim": Op(kind="replace_claim", claim_number=4, text="A rewritten claim 4."),
    "insert_section": Op(
        kind="insert_section",
        heading="Field of the Invention",
        paragraphs=["This invention relates to implantable devices."],
        position="before_claims",
    ),
    "delete_section": Op(kind="delete_section", heading="Claims"),
    "replace_text": Op(kind="replace_text", find="device", replace="apparatus"),
}
GENERATIVE = {"insert_claim", "replace_claim", "insert_section"}

# A document the graph secret must never reach a log line from.
DOC_SECRET = "ZZQQ-DOCUMENT-SECRET"
INSTRUCTION_SECRET = "ZZQQ-INSTRUCTION-SECRET"
PRIOR_ART_SECRET = "ZZQQ-PRIOR-ART-SECRET"
MODEL_PROSE_SECRET = "ZZQQ-MODEL-PROSE-SECRET"


@pytest.fixture(autouse=True)
def _dummy_key(ai_settings):
    """A syntactically valid but fake key, so `ai_enabled` is True by default.

    Set explicitly rather than inherited from `.env`: a developer with a real key must
    not see different behaviour from a clean clone, where the shipped placeholder makes
    every request a 503 before it reaches the graph. Nothing here can reach the network
    anyway — the runner is always overridden.
    """
    ai_settings(openai_api_key=DUMMY_KEY)


def chat_body(**overrides) -> dict:
    body = {
        "document_id": 1,
        "version_number": 1,
        "html": SEED_1,
        "instruction": "make claim 1 bold",
        "context_text": None,
        "context_name": None,
        "selection": None,
        "history": [],
        "consented": False,
        "pending_question": None,
        "clarify_count": 0,
    }
    body.update(overrides)
    return body


def edit(*ops: Op, message: str = "Made claim 1 bold.") -> GraphResult:
    return GraphResult(
        status="edit",
        message=message,
        operations=list(ops) or [OPS["format_claim"]],
        warnings=[],
        citations=[],
        options=[],
    )


def result(status: str, message: str = "ok", **kwargs) -> GraphResult:
    return GraphResult(
        status=status,
        message=message,
        operations=kwargs.get("operations", []),
        warnings=kwargs.get("warnings", []),
        citations=kwargs.get("citations", []),
        options=kwargs.get("options", []),
    )


def proposal_from(client: TestClient, fake_runner, *ops: Op, html: str = SEED_1) -> dict:
    """Get a real proposal by asking `/chat` for one, rather than hand-building it.
    Hand-building would let a field drift on the server without any test noticing, which
    is the failure mode the two-call design has."""
    fake_runner(edit(*ops))
    response = client.post("/api/ai/chat", json=chat_body(html=html, consented=False))
    assert response.status_code == 200, response.text
    return response.json()["proposal"]


@pytest.mark.parametrize(
    "status", ["applied", "proposal", "answer", "no_change", "needs_clarification", "error"]
)
def test_the_response_payload_can_never_contradict_its_status(status: str) -> None:
    """One `model_validator`, and the highest-value row here: every field is supplied on
    every status, so the validator is the only thing that can remove them.

    Nulling is asymmetric on purpose. Dropping a payload on a non-positive outcome fails
    CLOSED — the document cannot change — so it is silent. A positive outcome with a
    MISSING payload fails OPEN (a "proposal" the user cannot act on, or an "applied" that
    changes nothing while the UI says it did), so it raises.
    """
    a_proposal = AiProposal(
        proposal_id="x",
        document_id=1,
        version_number=1,
        base_sha256="abc",
        created_at=datetime.now(UTC),
        message="m",
        summary=["s"],
        authors_new_text=False,
        operations=[AiOperation.model_validate(OPS["format_claim"].model_dump())],
    )
    response = AiChatResponse(
        status=status,
        message="m",
        html="<p>x</p>",
        proposal=a_proposal,
        verification={"ok": True, "errors": [], "warnings": []},
        citations=[1],
        options=["Delete claim 1"],
    )
    assert (response.html is not None) == (status == "applied")
    assert (response.verification is not None) == (status == "applied")
    assert (response.proposal is not None) == (status == "proposal")
    assert (response.citations == [1]) == (status == "answer")
    assert (response.options == ["Delete claim 1"]) == (status == "needs_clarification")

    with pytest.raises(ValidationError):
        AiChatResponse(status="applied", message="x", html=None)
    with pytest.raises(ValidationError):
        AiChatResponse(status="proposal", message="x", proposal=None)
    with pytest.raises(ValidationError):
        AiApplyResponse(status="applied", message="x", html=None)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls, code: int):
    return cls("upstream", response=httpx.Response(code, request=_request()), body=None)


@pytest.mark.parametrize(
    ("label", "overrides", "raises", "code", "detail"),
    [
        ("html", {"html": "x" * 200_001}, None, 413, HTML_TOO_LARGE),
        ("context_text", {"context_text": "x" * 40_001}, None, 413, CONTEXT_TOO_LARGE),
        ("context_name", {"context_name": "n" * 121}, None, 413, CONTEXT_NAME_TOO_LONG),
        (
            "selection",
            {
                "selection": {
                    "text": "x" * 8_001,
                    "claim_numbers": [],
                    "whole_claims": False,
                    "truncated": False,
                }
            },
            None,
            413,
            SELECTION_TOO_LARGE,
        ),
        ("empty instruction", {"instruction": "   "}, None, 422, EMPTY_INSTRUCTION),
        ("long instruction", {"instruction": "x" * 2_001}, None, 422, INSTRUCTION_TOO_LONG),
        ("rate limit", {}, _status_error(openai.RateLimitError, 429), 429, BUSY_MESSAGE),
        ("auth", {}, _status_error(openai.AuthenticationError, 401), 502, KEY_REJECTED_MESSAGE),
        ("not found", {}, _status_error(openai.NotFoundError, 404), 502, MODEL_MISSING_MESSAGE),
        ("upstream", {}, _status_error(openai.APIStatusError, 500), 502, UPSTREAM_MESSAGE),
        ("api timeout", {}, openai.APITimeoutError(request=_request()), 504, TIMEOUT_MESSAGE),
        ("asyncio timeout", {}, TimeoutError(), 504, TIMEOUT_MESSAGE),
    ],
)
def test_the_chat_failure_table_returns_the_right_status_and_sentence(
    client: TestClient, fake_runner, label, overrides, raises, code, detail
) -> None:
    """The exception rows matter most: `APITimeoutError` subclasses `APIConnectionError`
    and four others subclass `APIStatusError`, so an except-chain in the wrong order
    collapses five distinct messages into one and every row here still returns 502."""
    fake_runner(edit(), raises=raises)
    response = client.post("/api/ai/chat", json=chat_body(**overrides))
    assert response.status_code == code, (label, response.text)
    assert response.json()["detail"] == detail, label


@pytest.mark.parametrize("key", ["", "sk-XXXXXXXX"])
def test_chat_is_503_without_a_usable_key(
    client: TestClient, fake_runner, ai_settings, key: str
) -> None:
    """The reviewer's most likely state: step 1 of the README is `cp .env.example .env`,
    and forgetting to paste a key must give a clean "AI is not configured", not an
    authentication 500."""
    ai_settings(openai_api_key=key)
    fake_runner(edit())
    response = client.post("/api/ai/chat", json=chat_body())
    assert response.status_code == 503
    assert response.json()["detail"] == NO_KEY_MESSAGE


def test_the_apply_failure_table_refuses_every_bad_proposal(
    client: TestClient, fake_runner
) -> None:
    """A proposal round-trips through an untrusted client, so every field is re-checked
    from scratch. The TTL is checked in BOTH directions: a one-sided check makes
    `created_at: "2999-01-01"` a proposal that never expires, which is the one property
    the TTL exists to deny."""
    good = proposal_from(client, fake_runner, OPS["format_claim"])

    def apply(**proposal_overrides):
        body = {"html": SEED_1, "proposal": {**good, **proposal_overrides}}
        return client.post("/api/ai/apply", json=body)

    oversized = client.post("/api/ai/apply", json={"html": "x" * 200_001, "proposal": good})
    assert oversized.status_code == 413 and oversized.json()["detail"] == HTML_TOO_LARGE

    assert apply(operations=[]).status_code == 422
    assert apply(operations=[]).json()["detail"] == NO_OPERATIONS
    assert apply(operations=good["operations"] * 21).json()["detail"] == TOO_MANY_TO_APPLY

    tampered = {**good["operations"][0], "claim_number": None}
    assert apply(operations=[tampered]).json()["detail"].startswith("This suggestion is no longer")

    expired = apply(created_at=(datetime.now(UTC) - timedelta(seconds=901)).isoformat())
    assert expired.status_code == 409 and expired.json()["detail"] == EXPIRED_PROPOSAL
    future = apply(created_at="2999-01-01T00:00:00+00:00")
    assert future.status_code == 409 and future.json()["detail"] == EXPIRED_PROPOSAL
    # A client clock a few seconds fast is normal and must still apply.
    skewed = apply(created_at=(datetime.now(UTC) + timedelta(seconds=5)).isoformat())
    assert skewed.status_code == 200, skewed.text

    drifted = client.post(
        "/api/ai/apply", json={"html": SEED_1 + "<p>typed since</p>", "proposal": good}
    )
    assert drifted.status_code == 409 and drifted.json()["detail"] == STALE_PROPOSAL


@pytest.mark.parametrize("consent", ["yes", "true", 1, "1"])
@pytest.mark.parametrize("claim_number", [True, "3", 3.0])
def test_a_tampered_boolean_or_claim_number_is_rejected_not_coerced(
    client: TestClient, fake_runner, consent, claim_number
) -> None:
    """Pydantic's lax coercion accepted the string "yes" as consent, and
    `{"claim_number": true}` as claim 1 — a real claim destroyed by a value that is not a
    claim number. Both fields decide something irreversible, so both are strict."""
    fake_runner(edit())
    assert client.post("/api/ai/chat", json=chat_body(consented=consent)).status_code == 422

    good = proposal_from(client, fake_runner, OPS["delete_claim"])
    tampered = {**good["operations"][0], "claim_number": claim_number}
    response = client.post(
        "/api/ai/apply", json={"html": SEED_1, "proposal": {**good, "operations": [tampered]}}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("kind", sorted(OPS))
def test_consent_is_per_version_and_covers_every_operation_kind(
    client: TestClient, fake_runner, kind: str
) -> None:
    """The prompt decision is one boolean supplied by the client and read in Python —
    never the operation kinds. A kind-driven design never prompted for `delete_claim`,
    the most destructive operation in the vocabulary; consent per version prompts once,
    for every kind.

    On an unconsented version the server never produces edited HTML at all: `html` is
    structurally absent rather than merely withheld.
    """
    fake_runner(edit(OPS[kind]))
    proposed = client.post("/api/ai/chat", json=chat_body(consented=False)).json()
    assert proposed["status"] == "proposal"
    assert proposed["html"] is None
    proposal = proposed["proposal"]
    assert [op["kind"] for op in proposal["operations"]] == [kind]
    assert proposal["base_sha256"] == content_hash(SEED_1)
    assert len(proposal["summary"]) == 1  # the card has a sentence for every kind
    assert proposal["authors_new_text"] is (kind in GENERATIVE)
    assert proposal["document_id"] == 1 and proposal["version_number"] == 1

    fake_runner(edit(OPS[kind]))
    applied = client.post("/api/ai/chat", json=chat_body(consented=True)).json()
    # delete_section("Claims") deliberately matches nothing, so it changes the document
    # in no way — every other kind does.
    assert applied["status"] == ("no_change" if kind == "delete_section" else "applied")
    assert applied["proposal"] is None

    # A mixed plan is proposed whole: the deterministic half is not applied early.
    fake_runner(edit(OPS[kind], OPS["insert_claim"]))
    mixed = client.post("/api/ai/chat", json=chat_body(consented=False)).json()
    assert mixed["status"] == "proposal" and mixed["html"] is None
    assert len(mixed["proposal"]["operations"]) == 2
    assert mixed["proposal"]["authors_new_text"] is True


def test_neither_route_can_write_to_the_database(client: TestClient, fake_runner) -> None:
    """Measured, not merely asserted about signatures — and then asserted about the
    signatures too, because that absence is the actual enforcement: there is no Session
    parameter for any code path through either handler to write with."""

    def version_rows() -> list[tuple]:
        rows = []
        for document_id in (1, 2):
            for summary in client.get(f"/api/documents/{document_id}/versions").json()["items"]:
                version = client.get(
                    f"/api/documents/{document_id}/versions/{summary['version_number']}"
                ).json()
                rows.append((document_id, version["content"], version["updated_at"]))
        return rows

    before = version_rows()
    proposal = proposal_from(client, fake_runner, OPS["insert_claim"])
    fake_runner(edit(OPS["format_claim"]))
    assert client.post("/api/ai/chat", json=chat_body(consented=True)).status_code == 200
    applied = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": proposal})
    assert applied.status_code == 200
    assert version_rows() == before

    for handler in (ai_chat, ai_apply):
        annotations = [p.annotation for p in inspect.signature(handler).parameters.values()]
        assert not any("Session" in str(a) for a in annotations), handler.__name__


def test_both_routes_apply_through_one_engine_and_apply_works_with_no_key(
    client: TestClient, fake_runner, ai_settings
) -> None:
    """The expected value is computed from the engine directly, so this fails if either
    route grows its own applier. `/apply` needing no key is the one-line proof that run 2
    is deterministic — if it needed one, it would be making a model call."""
    proposal = proposal_from(client, fake_runner, OPS["insert_claim"])
    applied = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": proposal})
    assert applied.status_code == 200, applied.text

    engine = apply_plan(SEED_1, [OPS["insert_claim"]])
    assert engine.html is not None
    assert applied.json()["html"] == sanitize_html(engine.html)

    fake_runner(edit(OPS["insert_claim"]))
    consented = client.post("/api/ai/chat", json=chat_body(consented=True))
    assert consented.json()["html"] == applied.json()["html"]

    ai_settings(openai_api_key="")
    assert client.post("/api/ai/chat", json=chat_body()).status_code == 503
    again = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": proposal})
    assert again.status_code == 200 and again.json()["status"] == "applied"


def test_no_change_outcomes_null_the_html(client: TestClient, fake_runner) -> None:
    """Three independent routes to `no_change`, all of which must null `html` or the
    client's dirty flag lies.

    The third is the subtle one: an apply that produces identical output from a
    NON-CANONICAL input. Against an `out == html` comparison it returned "applied" and
    silently pushed a canonicalised document into the editor, dirtying a buffer on a
    request that did nothing. Comparing in the engine's own space is what fixes it.
    """
    fake_runner(result("edit", "nothing to do"))
    empty = client.post("/api/ai/chat", json=chat_body(consented=True)).json()
    assert empty["status"] == "no_change" and empty["html"] is None
    assert empty["message"] == NOTHING_TO_CHANGE

    absent = Op(kind="replace_text", find="zzz-not-in-this-document", replace="x")
    fake_runner(edit(absent))
    ran = client.post("/api/ai/chat", json=chat_body(consented=True)).json()
    assert ran["status"] == "no_change" and ran["html"] is None
    assert ran["message"] == NO_VISIBLE_CHANGE

    wrapped = f"<div>{SEED_1}</div>"
    proposal = proposal_from(client, fake_runner, absent, html=wrapped)
    identical = client.post("/api/ai/apply", json={"html": wrapped, "proposal": proposal}).json()
    assert identical["status"] == "no_change" and identical["html"] is None


def test_a_plan_too_large_to_apply_or_to_save_is_refused(client: TestClient, fake_runner) -> None:
    """Two different caps, both previously missing.

    The operation count is a bound on blast radius: every extra operation multiplies the
    damage of one wrong claim number. The size cap bounded the INPUT only, so twenty
    operations carrying 95,000 characters each returned 1.9 million characters — ~10x the
    AI cap and ~2x the save cap, i.e. an edit the user is shown and can never save.
    """
    fake_runner(edit(*[OPS["format_claim"]] * 21))
    over = client.post("/api/ai/chat", json=chat_body(consented=True)).json()
    assert over["status"] == "error" and over["html"] is None
    assert over["message"] == TOO_MANY_OPERATIONS

    fake_runner(edit(*[OPS["format_claim"]] * 20))
    assert client.post("/api/ai/chat", json=chat_body(consented=True)).json()["status"] != "error"

    huge = [
        Op(kind="insert_claim", after_claim_number=2, text=f"A gadget {i}. " + "x" * 95_000)
        for i in range(20)
    ]
    proposal = proposal_from(client, fake_runner, *huge)
    payload = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": proposal}).json()
    assert payload["status"] == "error" and payload["html"] is None
    assert payload["message"] == RESULT_TOO_LARGE

    small = Op(kind="insert_claim", after_claim_number=2, text="A gadget. " + "x" * 1_000)
    inside = proposal_from(client, fake_runner, small)
    ok = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": inside})
    assert ok.json()["status"] == "applied"


@pytest.mark.parametrize("route", ["chat", "apply"])
def test_the_shipped_bytes_are_verified_and_sanitised_once_more(
    client: TestClient, fake_runner, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Two things the route does after the engine, on both paths.

    A verification failure on the bytes that actually ship blocks the edit rather than
    shipping it with a warning. And nh3 runs last, so `<script>alert(1)</script>` in the
    input never becomes a visible paragraph reading "alert(1)" — inert, but the AI path
    would be promoting to prose exactly what Save deletes.
    """
    html = "<script>alert(1)</script>" + TWO_CLAIMS
    proposal = proposal_from(client, fake_runner, OPS["format_claim"], html=html)
    cleaned = client.post("/api/ai/apply", json={"html": html, "proposal": proposal}).json()
    assert cleaned["status"] == "applied"
    assert "alert(1)" not in cleaned["html"]
    assert cleaned["html"] == sanitize_html(cleaned["html"])

    from app.ai.verify import VerifyReport

    blocked = VerifyReport(ok=False, errors=["The edit broke the claim numbering."], warnings=[])
    if route == "apply":
        good = proposal_from(client, fake_runner, OPS["format_claim"])
        monkeypatch.setattr(ai_module, "verify", lambda *a, **k: blocked)
        response = client.post("/api/ai/apply", json={"html": SEED_1, "proposal": good})
    else:
        fake_runner(edit())
        monkeypatch.setattr(ai_module, "verify", lambda *a, **k: blocked)
        response = client.post("/api/ai/chat", json=chat_body(consented=True))

    payload = response.json()
    assert payload["status"] == "error" and payload["html"] is None
    assert payload["message"] == "The edit broke the claim numbering."


@pytest.mark.parametrize("route", ["chat", "apply"])
def test_a_deletion_never_warns_twice_about_one_claim(
    client: TestClient, fake_runner, route: str
) -> None:
    """The route's second `verify` must be told what the plan deleted.

    Deleting claim 1 of two leaves old claim 2 — now claim 1 — reading "of claim 1". That
    is the renumber's doing, so the self-reference warning is suppressed and only the
    dangling one is reported. Omitting `deleted_numbers` on the second pass brought the
    false one back, and the user read two contradictory sentences about the same claim.
    """
    op = Op(kind="delete_claim", claim_number=1)
    if route == "apply":
        proposal = proposal_from(client, fake_runner, op, html=TWO_CLAIMS)
        response = client.post("/api/ai/apply", json={"html": TWO_CLAIMS, "proposal": proposal})
    else:
        fake_runner(edit(op))
        response = client.post("/api/ai/chat", json=chat_body(html=TWO_CLAIMS, consented=True))
    assert response.status_code == 200, response.text

    warnings = response.json()["warnings"]
    assert warnings == ["Claim 1 still refers to claim 1, which was deleted."]
    # The engine already said exactly that: the route may add nothing it did not know.
    assert warnings == apply_plan(TWO_CLAIMS, [op]).warnings


def test_the_request_is_capped_truncated_and_clamped_rather_than_rejected(
    client: TestClient, fake_runner
) -> None:
    """What the route does to a request before the graph sees it.

    A long conversation is TRUNCATED, never a 422. The instruction's length is measured
    on the STRIPPED value, or 1,995 real characters plus a newline is rejected as "too
    long (limit 2,000)". The selection and the filename reach the runner and never the
    document — and `clarify_count` is clamped at BOTH ends, because 0 is the lower one
    and the client supplies it.
    """
    fake_runner(edit())
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(20)
    ]
    selection = {
        "text": "A wireless optogenetic device",
        "claim_numbers": [1],
        "whole_claims": False,
        "truncated": False,
    }
    body = chat_body(
        history=history,
        selection=selection,
        instruction="x" * 1_995 + "  \n  ",
        context_text=f"Prior art: {PRIOR_ART_SECRET}",
        context_name="prior.txt",
    )
    assert client.post("/api/ai/chat", json=body).status_code == 200

    sent = fake_runner.calls[-1]
    assert [t.content for t in sent.history] == [f"turn {i}" for i in range(14, 20)]
    assert sent.instruction == "x" * 1_995
    assert sent.selection is not None and sent.selection.claim_numbers == [1]
    assert sent.prior_art_name == "prior.txt"
    assert sent.html == SEED_1  # the document is untouched by any of it

    for sent_count, expected in ((-5, 0), (0, 0), (1, 1), (99, 2)):
        fake_runner(result("clarification", "Which claim?"))
        assert (
            client.post("/api/ai/chat", json=chat_body(clarify_count=sent_count)).status_code == 200
        )
        assert fake_runner.calls[-1].clarify_count == expected


def test_a_client_reporting_its_own_clarify_budget_is_overruled_by_the_transcript(
    client: TestClient,
) -> None:
    """The clamp bounds the upper end, and 0 is the lower one — so a client sending 0
    every turn would receive an unbounded sequence of questions, one `understand` call
    each. `clarify_floor` re-derives the count from the transcript, which is the SAME
    evidence the model is given: a client that strips its own questions to lower the
    floor also destroys the context that makes its conversation work."""

    def runner_that_obeys_the_budget(graph_input):
        if graph_input.clarify_count >= 2:
            return result("no_change", CAPABILITY_STATEMENT)
        return result("clarification", "Which claim did you mean?")

    client.app.dependency_overrides[ai_module.get_ai_runner] = lambda: runner_that_obeys_the_budget
    history = [
        {"role": "user", "content": "make it bold"},
        {"role": "assistant", "content": "Which claim did you mean?"},
        {"role": "user", "content": "the other one"},
        {"role": "assistant", "content": "Which claim did you mean?"},
    ]
    payload = client.post("/api/ai/chat", json=chat_body(history=history, clarify_count=0)).json()

    assert payload["status"] == "no_change"
    assert payload["message"] == CAPABILITY_STATEMENT
    assert payload["options"] == []
    client.app.dependency_overrides.clear()


def test_read_only_outcomes_carry_only_what_they_should(client: TestClient, fake_runner) -> None:
    """An answer carries citations and no HTML; a clarification carries options, and
    nothing else does — asserted over the wire rather than on the model alone."""
    fake_runner(result("answer", "Claim 7 depends on claim 5.", citations=[5, 7]))
    answered = client.post("/api/ai/chat", json=chat_body(consented=True)).json()
    assert answered["status"] == "answer"
    assert answered["message"] == "Claim 7 depends on claim 5."
    assert answered["html"] is None and answered["proposal"] is None
    assert answered["citations"] == [5, 7] and answered["options"] == []

    fake_runner(result("clarification", "Which claim?", options=["Delete claim 1"]))
    asked = client.post("/api/ai/chat", json=chat_body()).json()
    assert asked["status"] == "needs_clarification"
    assert asked["options"] == ["Delete claim 1"]

    fake_runner(result("answer", "It depends on claim 1.", options=["Delete claim 1"]))
    assert client.post("/api/ai/chat", json=chat_body()).json()["options"] == []


def test_no_request_payload_reaches_a_log_line(client: TestClient, fake_runner, caplog) -> None:
    """The document being processed is a customer's unpublished patent application, so a
    log line is a disclosure. Lengths, counts, flags and a truncated digest only.

    The entry line is the one that sees the whole request at once — document, instruction,
    selection and uploaded file, in one object — and the `req=` it mints is what
    correlates a /chat line to the /apply that may follow it.
    """
    fake_runner(result("no_change", f"Nothing to change, {MODEL_PROSE_SECRET}"))
    html = f"<h1>Claims</h1><p>1. A system comprising {DOC_SECRET}.</p><p>2. Of claim 1.</p>"

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/api/ai/chat",
            json=chat_body(
                html=html,
                instruction=f"Do something, {INSTRUCTION_SECRET}",
                context_text=f"Prior art: {PRIOR_ART_SECRET}",
                context_name="prior.txt",
                consented=True,
            ),
        )

    assert response.status_code == 200
    logged = "\n".join(
        [r.getMessage() for r in caplog.records] + [r.exc_text or "" for r in caplog.records]
    )
    for secret in (DOC_SECRET, INSTRUCTION_SECRET, PRIOR_ART_SECRET, MODEL_PROSE_SECRET):
        assert secret not in logged, f"{secret} reached a log line"

    entry = next(m for m in logged.split("\n") if m.startswith("ai.request req="))
    done = next(m for m in logged.split("\n") if m.startswith("ai.done req="))
    assert entry.split()[1] == done.split()[1]
    assert fake_runner.calls[0].req == entry.split()[1].removeprefix("req=")
    assert f"html_chars={len(html)}" in entry and "has_file=True" in entry
