"""IM1-IM16 — importing a patent someone already wrote, and the messy real cases.

The list of inputs here is not imagination: it is what a `.txt` patent actually looks like
after it has been through Word, a PDF extractor, a Windows editor and an email client.
Every one of them must produce a document that parses, renders and re-parses identically,
or the safety net the whole AI layer rests on is gone for imported documents.
"""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.ai.document import parse, render
from app.routers.imports import EMPTY_FILE
from app.sanitize import sanitize_html
from app.textimport import ImportResult, import_text, normalise

SIMPLE = """\
Widget With Improved Frobnicator

BACKGROUND

Frobnicators of the prior art are slow.

CLAIMS

1. A widget comprising a frobnicator.
2. The widget of claim 1, wherein the frobnicator is glass.
"""


def claim_texts(result: ImportResult) -> list[str]:
    doc = parse(result.html)
    return [c.blocks[0].html for c in doc.claims]


def assert_round_trips(result: ImportResult) -> None:
    """THE contract. `render(parse(x)) == x` is what makes an imported document safe to
    edit with the AI; asserting it on every case below is cheaper than discovering which
    input broke it after an edit has already been applied."""
    once = render(parse(result.html))
    assert once == result.html
    assert render(parse(once)) == once
    # And the save path will not change the bytes the user previewed.
    assert sanitize_html(result.html) == result.html


# ----------------------------------------------------------------- the ordinary case


def test_IM1_a_plain_patent_imports_with_title_heading_and_claims() -> None:
    result = import_text(SIMPLE)
    assert result.title == "Widget With Improved Frobnicator"
    # The title is NOT in the content: getHTML() returns body only, so a title left in
    # the content column is destroyed on the first save.
    assert "Widget With Improved" not in result.html
    assert result.html.startswith("<h1>BACKGROUND</h1>")
    assert result.claim_count == 2
    assert result.notes == []
    assert_round_trips(result)


def test_IM2_claim_numbers_are_a_field_not_text() -> None:
    """Invariant 4. The prefix must be readable by the parser as a NUMBER, so that
    renumbering never edits the user's words."""
    doc = parse(import_text(SIMPLE).html)
    assert [c.number for c in doc.claims] == [1, 2]
    assert [c.separator for c in doc.claims] == [".", "."]
    # Stripped from the text on parse, re-injected on render.
    assert not doc.claims[0].blocks[0].html.startswith("1.")
    assert doc.claims[0].blocks[0].html.startswith("A widget")


# ------------------------------------------------------------------ the messy cases


def test_IM3_hard_wrapped_lines_are_rejoined() -> None:
    """A PDF extractor wraps at 72 columns. Left alone, every wrapped line becomes its
    own paragraph and a two-claim patent parses as fourteen."""
    result = import_text(
        "CLAIMS\n\n1. A widget comprising\n   a frobnicator and\n   a housing.\n"
        "2. The widget of claim 1.\n"
    )
    assert claim_texts(result) == [
        "A widget comprising a frobnicator and a housing.",
        "The widget of claim 1.",
    ]
    assert_round_trips(result)


def test_IM4_a_new_claim_ends_the_previous_paragraph() -> None:
    """Claims separated by a single newline rather than a blank line. Joining them would
    produce ONE paragraph holding every claim, which parses as no claims at all."""
    result = import_text("CLAIMS\n1. A widget.\n2. A second widget.\n3. A third widget.\n")
    assert result.claim_count == 3


def test_IM5_no_claims_heading_gets_one_and_says_so() -> None:
    result = import_text("Some Patent\n\n1. A widget.\n\n2. The widget of claim 1.\n")
    assert result.claim_count == 2
    assert "<h1>Claims</h1>" in result.html
    assert any('No "Claims" heading was found' in n for n in result.notes)
    assert_round_trips(result)


@pytest.mark.parametrize("separator", [".", ")"])
def test_IM6_both_claim_separators_are_recognised_and_preserved(separator: str) -> None:
    """A `1) 2) 3)` document stays a `1) 2) 3)` document — the separator is a field too."""
    body = f"CLAIMS\n\n1{separator} A widget.\n\n2{separator} The widget of claim 1.\n"
    result = import_text(body)
    assert result.claim_count == 2
    assert [c.separator for c in parse(result.html).claims] == [separator, separator]
    assert_round_trips(result)


def test_IM7_duplicate_claim_numbers_are_reported_never_repaired() -> None:
    """Renumbering on import would rewrite a legal document nobody asked us to touch, and
    the cross-references would then point at the old numbers."""
    result = import_text("CLAIMS\n\n1. A widget.\n\n2. Second.\n\n2. Also second.\n\n5. Fifth.\n")
    assert [c.number for c in parse(result.html).claims] == [1, 2, 2, 5]
    assert any("2 appears more than once" in n for n in result.notes)
    assert any("skips (2 to 5)" in n for n in result.notes)
    assert_round_trips(result)


def test_IM8_out_of_order_claims_are_reported() -> None:
    result = import_text("CLAIMS\n\n3. Third.\n\n1. First.\n\n2. Second.\n")
    assert any("not in ascending order" in n for n in result.notes)


def test_IM9_a_file_that_is_not_a_patent_imports_as_body_text_and_says_so() -> None:
    result = import_text("milk\n\nbread\n\ncheese\n")
    assert parse(result.html).claims == []
    assert any("No numbered claims were recognised" in n for n in result.notes)
    assert "bread" in result.html
    assert_round_trips(result)


def test_IM10_a_lone_numbered_paragraph_is_not_a_claim_set() -> None:
    """The parser's >=2 rule, stated to the user. "1. Field of the Invention" at the top
    of a Background is a numbered sentence, not claim 1."""
    result = import_text("Some Patent\n\nBACKGROUND\n\n1. Field of the Invention is widgets.\n")
    assert result.claim_count == 0
    assert any("Only one numbered paragraph" in n for n in result.notes)
    # And the digits survive: parse strips a prefix it decides is not a claim number and
    # must put it back, or import silently destroys text it merely failed to recognise.
    assert "1. Field of the Invention" in result.html
    assert_round_trips(result)


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("A\r\nB\r\n\r\n1. One.\r\n\r\n2. Two.\r\n", "windows line endings"),
        ("﻿A\n\n1. One.\n\n2. Two.\n", "byte order mark"),
        ("A\n\n1.\tOne.\n\n2.\tTwo.\n", "tabs"),
        ("A\n\n1. One.\x00\n\n2. Two.\n", "a stray NUL"),
        ("A\n\n1. One ​.\n\n2. Two.\n", "a zero-width space"),
    ],
)
def test_IM11_encoding_debris_does_not_change_the_result(raw: str, why: str) -> None:
    result = import_text(raw)
    assert result.claim_count == 2, why
    assert "\r" not in result.html and "\x00" not in result.html and "\t" not in result.html
    assert_round_trips(result)


def test_IM12_emoji_and_rtl_survive_and_stay_escaped() -> None:
    """Categories S and L, not C — a patent may legitimately quote either, and stripping
    them would be data loss. `<` in the user's text must come back escaped."""
    result = import_text("CLAIMS\n\n1. A widget 🔬 with العربية and a < b.\n\n2. Second.\n")
    assert "🔬" in result.html and "العربية" in result.html
    assert "&lt; b" in result.html  # escaped exactly once
    assert_round_trips(result)


def test_IM13_an_empty_file_produces_an_empty_patent_not_a_crash() -> None:
    result = import_text("   \n\n  \n")
    assert result.html == ""
    assert result.claim_count == 0
    assert any("no readable text" in n for n in result.notes)


def test_IM14_a_one_megabyte_file_imports() -> None:
    """The save cap is 1,000,000 bytes, so this is the largest importable document."""
    filler = "comprising a frobnicator, a housing and a gasket of biocompatible material. " * 14
    claims = "\n\n".join(f"{n}. A widget of type {n} {filler}" for n in range(1, 901))
    raw = f"Big Patent\n\nCLAIMS\n\n{claims}\n"
    assert len(raw.encode()) > 900_000
    result = import_text(raw)
    assert result.claim_count == 900
    assert_round_trips(result)


def test_IM15_claim_numbers_above_999_are_not_claims_and_that_is_the_parser_rule() -> None:
    """`CLAIM_PREFIX_RE` is `\\d{1,3}` so that "2024. In the prior art…" is a year and not
    claim 2024. The importer inherits the bound rather than defining a second one — and a
    document with a thousand claims does not exist, while a document with a year in it
    does."""
    result = import_text("CLAIMS\n\n1. First.\n\n2. Second.\n\n1000. Not a claim.\n")
    assert [c.number for c in parse(result.html).claims] == [1, 2]
    assert "1000. Not a claim." in result.html  # kept as text; nothing is destroyed
    assert_round_trips(result)


def test_IM15_markup_in_the_source_text_is_escaped_never_executed() -> None:
    result = import_text("CLAIMS\n\n1. A <script>alert(1)</script> widget.\n\n2. Second.\n")
    assert "<script>" not in result.html
    assert "alert(1)" in result.html  # escaped to text, not silently deleted
    assert_round_trips(result)


def test_IM16_normalise_keeps_blank_lines_which_are_the_paragraph_separator() -> None:
    assert normalise("a\r\n\r\nb") == "a\n\nb"


# ------------------------------------------------------------------------ the route


def test_IM17_the_route_returns_a_preview_and_writes_nothing(client: TestClient) -> None:
    before = client.get("/api/documents").json()["total"]
    response = client.post("/api/import/text", json={"text": SIMPLE, "filename": "widget.txt"})
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["claim_count"] == 2
    assert body["title"] == "Widget With Improved Frobnicator"
    assert client.get("/api/documents").json()["total"] == before  # no db write


def test_IM18_the_filename_supplies_a_title_when_the_text_has_none(client: TestClient) -> None:
    """A file that opens straight into its claims has no title line to take."""
    response = client.post(
        "/api/import/text",
        json={
            "text": "1. A widget.\n\n2. The widget of claim 1.\n",
            "filename": "US_10123456_B2.txt",
        },
    )
    assert response.json()["title"] == "US 10123456 B2"


def test_IM19_an_empty_file_is_a_422_with_a_readable_sentence(client: TestClient) -> None:
    response = client.post("/api/import/text", json={"text": "   \n"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"] == EMPTY_FILE


def test_IM20_an_oversized_file_is_a_413_with_the_numbers_in_it(client: TestClient) -> None:
    response = client.post("/api/import/text", json={"text": "x" * 1_000_001})
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert "1000001" in response.json()["detail"]


def test_IM21_an_imported_preview_is_accepted_verbatim_by_the_save_path(
    client: TestClient,
) -> None:
    """The end of the journey, and the reason the conversion sanitises before it returns:
    what the user previewed is what the database stores, byte for byte."""
    preview = client.post("/api/import/text", json={"text": SIMPLE}).json()
    created = client.post(
        "/api/documents", json={"title": preview["title"], "content": preview["content"]}
    )
    assert created.status_code == status.HTTP_201_CREATED
    stored = client.get(f"/api/documents/{created.json()['id']}/versions/1").json()
    assert stored["content"] == preview["content"]
