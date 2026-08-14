"""Bind, apply, renumber, remap — the engine's only public write path.

Six steps, in an order that may not be changed, and one idea holding them together:
**every claim reference is bound to a uid against the ORIGINAL parse, before anything
mutates.** That is what makes `[delete 3, delete 5]` delete what the user saw.

    naive:  1 2 3 4 5 6 → delete #3 → 1 2 4 5 6 → delete #5 → deleted old claim 6  ✗
    bound:  3→uid_c and 5→uid_e locked in first → delete both → renumber           ✓

`apply_plan` returns `ApplyResult`, whose `html` is None on every refusal path. That is
what makes CLAUDE.md invariant 3 hold by construction rather than by client discipline:
the client cannot call setContent on an edit the engine refused, because there is no
HTML to call it with.

Pure. Never touches the database (invariant 2). Imports neither `openai` nor `langgraph`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.document import REF_RE, Block, ParsedDocument, all_blocks, parse, render
from app.ai.operations import OPS, ApplyCtx
from app.ai.schemas import Op, PlanError, require
from app.ai.verify import VerifyReport, verify

W_DUPLICATE_NUMBER = "Claim number {number} appears more than once; the first one was used."
W_DANGLING_REF = "Claim {number} still refers to claim {old}, which was deleted."

# Fixed order. Python's sort is STABLE, so plan order is preserved within a kind:
#   sorted(operations, key=lambda o: KIND_ORDER.index(o.kind))
# Three of these adjacencies are necessary and one is a choice. Which is which:
#
#   replace_claim BEFORE format_claim  — NECESSARY. "Rewrite claim 2 and make it bold":
#       replace_claim rebuilds the blocks and discards marks, so reversed, the bold is
#       silently lost. A14(a).
#   insert_claim BEFORE delete_claim   — NECESSARY. "Replace claim 3 with a broader
#       version" is insert-after-3 plus delete-3. Delete first and the anchor is gone,
#       the insert is skipped, and the user loses a claim with a warning to show for it.
#       A14(b).
#   insert_section LAST                — NECESSARY-ish. It is the only op that can
#       synthesise a claims heading, so running it last means it observes the final
#       structure and can never shift an index an earlier op depended on. A7.
#   replace_text FIRST                 — ARBITRARY. It does not see text this plan
#       introduced. Chosen so text edits apply to what the user was looking at. A
#       trade-off, documented as a choice rather than dressed up as a derivation.
KIND_ORDER = (
    "replace_text",
    "replace_claim",
    "format_claim",
    "insert_claim",
    "delete_claim",
    "insert_section",
)


@dataclass
class ApplyResult:
    html: str | None  # None when the plan was refused OR verification failed
    warnings: list[str]
    report: VerifyReport
    # The ORIGINAL numbers this plan deleted. Carried out of the engine because a caller
    # that verifies the SHIPPED bytes a second time needs it to keep VF-W2's suppression:
    # without it the renumber's own self-reference is reported back to the user as a
    # defect they caused, next to the correct warning that contradicts it.
    deleted_numbers: frozenset[int] = frozenset()


def _blocked(message: str) -> ApplyResult:
    """A refusal expressed in the type the caller already handles."""
    return ApplyResult(
        html=None, warnings=[], report=VerifyReport(ok=False, errors=[message], warnings=[])
    )


def _bind(doc: ParsedDocument, warnings: list[str]) -> dict[int, int]:
    """Step 2 — the number→uid table, first-wins, in document order.

    A duplicated number warns ONCE, here, when the table is built — not once per
    occurrence and not once per operation that resolves through it.
    """
    uid_by_number: dict[int, int] = {}
    duplicated: set[int] = set()
    for claim in doc.claims:
        if claim.number in uid_by_number:
            if claim.number not in duplicated:
                duplicated.add(claim.number)
                warnings.append(W_DUPLICATE_NUMBER.format(number=claim.number))
            continue
        uid_by_number[claim.number] = claim.uid
    return uid_by_number


def _remap_references(
    doc: ParsedDocument,
    original_number_by_uid: dict[int, int],
    deleted_numbers: set[int],
    warnings: list[str],
) -> None:
    """Step 6 — rewrite cross-references to the numbers the claims now carry.

    C10: the obvious implementation is the wrong one. A loop of `str.replace` over the
    mapping DOUBLE-APPLIES — deleting claim 3 gives the chain 4→3, 5→4, 6→5, 7→6, 8→7,
    which a sequential loop cascades end to end down to 3. One `re.sub` with a callable
    is non-negotiable.

    This covers text authored by the SAME plan (C12): an insert_claim whose text says
    "of claim 2" was written against the numbering the model was shown, so inserting
    anywhere but at the end would otherwise point the new claim at the wrong parent.
    """
    old_to_new = {
        original_number_by_uid[c.uid]: c.number
        for c in doc.claims
        if c.uid in original_number_by_uid  # inserted claims had no old number to map from
    }

    def substitute(match: re.Match[str]) -> str:
        old = int(match.group(3))
        new = old_to_new.get(old)
        if new is None:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{new}"

    # Rule 1: a reference to a deleted claim is left verbatim and warned, never guessed.
    # Guessing an author's intent in a legal document is worse than flagging it. The
    # warning names the referring claim's NEW number, because that is what the user will
    # see when they read it — which is why this runs after the renumber.
    for claim in doc.claims:
        for block in claim.blocks:
            for match in REF_RE.finditer(block.html):
                old = int(match.group(3))
                if old in deleted_numbers and old not in old_to_new:
                    warnings.append(W_DANGLING_REF.format(number=claim.number, old=old))

    for block in all_blocks(doc):
        block.html = REF_RE.sub(substitute, block.html)


def _run(html: str, operations: list[Op]) -> tuple[str, list[str], int, set[int]]:
    warnings: list[str] = []

    doc = parse(html)  # 1
    original_number_by_uid = {c.uid: c.number for c in doc.claims}  # 2
    ctx = ApplyCtx(uid_by_number=_bind(doc, warnings))

    # 3+4. Binding happens inside each adapter, against `ctx.uid_by_number`, which was
    # built from the untouched parse — so after this point no operation is working from a
    # claim NUMBER. Sorting is stable, so plan order survives within a kind.
    for op in sorted(operations, key=lambda o: KIND_ORDER.index(o.kind)):
        OPS[op.kind](doc, op, ctx, warnings)

    for i, claim in enumerate(doc.claims, 1):  # 5 — exactly once, never inside an operation
        claim.number = i

    # 5a. The >=2 fallback is not symmetric under deletion: a heading-less two-claim
    # document that loses a claim re-parses as ZERO claims, VF-E4 fires, and the edit is
    # refused — so the user could never reach a single-claim document at all. The document
    # is not corrupt, merely no longer self-describing. Give it a heading, exactly as
    # insert_section does for the same reason (C9).
    if doc.claims and doc.claims_heading is None and len(doc.claims) < 2:
        doc.claims_heading = Block("h1", "Claims")

    _remap_references(doc, original_number_by_uid, ctx.deleted_numbers, warnings)  # 6
    return render(doc), warnings, len(doc.claims), ctx.deleted_numbers


def apply_plan(html: str, operations: list[Op]) -> ApplyResult:
    """Apply a plan to a document. Pure; never touches the database (invariant 2).

    The ONLY public entry point of the engine's write path.
    """
    try:
        # Step 0. apply_plan validates its own input, because its callers are a FastAPI
        # handler and a graph node, and neither is a place where a malformed Op should
        # become a stack trace. Both routes also call require() at their own layer, to
        # tell a model failure (200 error) from an untrusted-client failure (422) — this
        # is what makes the engine unreachable without validation from ANY caller.
        for op in operations:
            require(op)
    except PlanError as exc:
        return _blocked(str(exc))

    out, warnings, expected, deleted = _run(html, operations)
    # `expected` is the applier's OWN count. Deriving it by re-parsing the output would
    # make VF-E4 vacuous, which is the entire point of that check.
    report = verify(html, out, expected_claims=expected, deleted_numbers=frozenset(deleted))
    return ApplyResult(
        html=out if report.ok else None,
        warnings=list(dict.fromkeys(warnings + report.warnings)),
        report=report,
        deleted_numbers=frozenset(deleted),
    )
