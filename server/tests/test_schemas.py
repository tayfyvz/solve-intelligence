"""P1-P9 — every contract between a model and the engine.

The recurring theme: a constraint expressed in a JSON schema is a constraint we cannot
verify the API accepts, so every bound lives in Python and a test enforces that split
rather than a convention nobody remembers.
"""

import json
from pathlib import Path
from typing import get_args

import pytest
from openai.lib._pydantic import to_strict_json_schema

from app.ai.operations import OPS
from app.ai.schemas import (
    MAX_CLAIM_NUMBER,
    MAX_SECTION_PARAGRAPHS,
    REQUIRED,
    Answer,
    Citation,
    EditPlan,
    JudgeVerdict,
    Op,
    OpKind,
    PlanError,
    Understanding,
    authors_new_text,
    content_hash,
    judge_failed,
    require,
)
from app.ai.summary import summarise

PLANNER_MODELS = [EditPlan, Op, Understanding, JudgeVerdict, Answer, Citation]
CONSTRAINT_KEYWORDS = ("minimum", "maximum", "minLength", "maxLength", "pattern")

OPTIONAL_OP_FIELDS = [
    "claim_number",
    "after_claim_number",
    "mark",
    "enabled",
    "text",
    "heading",
    "paragraphs",
    "position",
    "find",
    "replace",
]


@pytest.mark.parametrize("model", PLANNER_MODELS, ids=lambda m: m.__name__)
def test_all_planner_models_strict(model) -> None:
    """P1 and P2 — every planner-facing model builds a strict schema, and none of them
    smuggles in a constraint keyword whose acceptance we cannot verify without a key."""
    schema = json.dumps(to_strict_json_schema(model))
    for keyword in CONSTRAINT_KEYWORDS:
        assert f'"{keyword}"' not in schema, f"{model.__name__} carries {keyword}"


def test_required_covers_every_op_kind() -> None:
    """P3 — a new kind without a validation row fails here, not in production."""
    assert set(REQUIRED) == set(get_args(OpKind))


def test_ops_covers_every_op_kind() -> None:
    """P9 — the OPS twin of P3, and the reason `apply_plan` may write the bare subscript
    OPS[op.kind]. A kind with a REQUIRED row and no OPS row is a KeyError inside a
    request handler: a 500 on a well-formed request."""
    assert set(OPS) == set(get_args(OpKind))


def test_summaries_cover_every_op_kind() -> None:
    """The proposal card must have a sentence for every kind, or Proceed renders a crash."""
    for kind in get_args(OpKind):
        assert summarise(_sample(kind))


def _sample(kind: str) -> Op:
    samples = {
        "format_claim": Op(kind="format_claim", claim_number=3, mark="bold", enabled=True),
        "delete_claim": Op(kind="delete_claim", claim_number=3),
        "insert_claim": Op(kind="insert_claim", after_claim_number=2, text="A new claim."),
        "replace_claim": Op(kind="replace_claim", claim_number=3, text="Rewritten."),
        "insert_section": Op(
            kind="insert_section", heading="Background", paragraphs=["x"], position="before_claims"
        ),
        "replace_text": Op(kind="replace_text", find="glass", replace="quartz"),
    }
    return samples[kind]


@pytest.mark.parametrize(
    ("op", "missing"),
    [
        (Op(kind="delete_claim"), "claim_number"),
        (Op(kind="format_claim", claim_number=1, enabled=True), "mark"),
        (Op(kind="insert_section", heading="H", paragraphs=[]), "position"),
    ],
)
def test_require_reports_missing_fields(op: Op, missing: str) -> None:
    """P4."""
    with pytest.raises(PlanError) as excinfo:
        require(op)
    assert missing in str(excinfo.value)


def test_require_enforces_bounds() -> None:
    """P5 — the bounds the schema deliberately does not carry."""
    with pytest.raises(PlanError):
        require(Op(kind="delete_claim", claim_number=MAX_CLAIM_NUMBER + 1))

    # 0 is only meaningful for after_claim_number; the operations layer warns about it,
    # so `require` lets it through rather than duplicating that judgement.
    require(Op(kind="delete_claim", claim_number=0))

    with pytest.raises(PlanError):
        require(
            Op(
                kind="insert_section",
                heading="H",
                paragraphs=["p"] * (MAX_SECTION_PARAGRAPHS + 1),
                position="before_claims",
            )
        )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("format_claim", False),
        ("delete_claim", False),
        ("replace_text", False),
        ("insert_claim", True),
        ("replace_claim", True),
        ("insert_section", True),
    ],
)
def test_authors_new_text_is_kind_driven(kind: str, expected: bool) -> None:
    """P6 — computed from the operation kinds, never from anything a model says about
    itself."""
    plan = EditPlan(status="ok", message="m", operations=[_sample(kind)])
    assert authors_new_text(plan) is expected


def test_authors_new_text_on_mixed_and_empty_plans() -> None:
    """P6."""
    mixed = EditPlan(
        status="ok",
        message="m",
        operations=[_sample("delete_claim"), _sample("insert_claim")],
    )
    assert authors_new_text(mixed) is True
    assert authors_new_text(EditPlan(status="ok", message="m", operations=[])) is False


def test_needs_confirmation_is_gone() -> None:
    """P6 — the old name decided nothing after the consent redesign, and a function whose
    name lies is worse than no function at all. It must not come back."""
    hits = [
        path
        for path in (Path(__file__).parents[1] / "app").rglob("*.py")
        if "needs_confirmation" in path.read_text()
    ]
    assert hits == []


@pytest.mark.parametrize("field_name", OPTIONAL_OP_FIELDS)
def test_optional_fields_are_nullable_and_required(field_name: str) -> None:
    """P7 — strict mode puts every field in `required`, so an optional one must express
    its optionality as a nullable type instead."""
    schema = to_strict_json_schema(Op)
    assert field_name in schema["required"]
    assert "null" in json.dumps(schema["properties"][field_name])


def test_judge_failed_ignores_empty_failures() -> None:
    """P8 — a judge that says "fail" but cannot name a defect gives `draft` nothing to
    act on, so the retry would be identical and would burn a call for nothing."""
    assert judge_failed(JudgeVerdict(verdict="fail", failures=[], suggestion="")) is False
    assert judge_failed(JudgeVerdict(verdict="fail", failures=["no antecedent"], suggestion="x"))
    assert judge_failed(JudgeVerdict(verdict="pass", failures=[], suggestion="")) is False


def test_content_hash_is_the_only_hash_and_is_stable() -> None:
    """Two copies of a hash do not diverge loudly; they diverge into a 409 on every
    apply, or into a check that silently stops checking."""
    assert content_hash("<p>a</p>") == content_hash("<p>a</p>")
    assert content_hash("<p>a</p>") != content_hash("<p>b</p>")
    assert len(content_hash("")) == 64
