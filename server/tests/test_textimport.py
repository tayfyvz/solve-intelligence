"""Importing a patent someone already wrote.

The inputs here are not imagination: they are what a `.txt` patent looks like after Word,
a PDF extractor, a Windows editor and an email client. Every one must produce a document
that parses, renders and re-parses identically, or the safety net the whole AI layer
rests on is gone for imported documents.
"""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.ai.document import parse, render
from app.routers.imports import EMPTY_FILE
from app.sanitize import sanitize_html
from app.textimport import ImportResult, import_text

SIMPLE = """\
Widget With Improved Frobnicator

BACKGROUND

Frobnicators of the prior art are slow.

CLAIMS

1. A widget comprising a frobnicator.
2. The widget of claim 1, wherein the frobnicator is glass.
"""


def assert_round_trips(result: ImportResult) -> None:
    """The contract. `render(parse(x)) == x` is what makes an imported document safe to
    edit with the AI, and the sanitiser must not change the bytes the user previewed —
    otherwise what they approved and what the database holds differ."""
    once = render(parse(result.html))
    assert once == result.html
    assert render(parse(once)) == once
    assert sanitize_html(result.html) == result.html


def test_a_plain_patent_imports_with_its_title_out_of_the_content() -> None:
    """`getHTML()` returns body content only, so a title left in the content column is
    destroyed on the first save. It goes to `Document.title` or it does not exist.

    The claim number is a field, never text: stripped on parse, re-injected on render, so
    renumbering can never edit the user's words.
    """
    result = import_text(SIMPLE)
    assert result.title == "Widget With Improved Frobnicator"
    assert "Widget With Improved" not in result.html
    assert result.html.startswith("<h1>BACKGROUND</h1>")
    assert result.claim_count == 2 and result.notes == []
    assert_round_trips(result)

    doc = parse(result.html)
    assert [c.number for c in doc.claims] == [1, 2]
    assert doc.claims[0].blocks[0].html.startswith("A widget")


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        # A PDF extractor wraps at 72 columns. Left alone, every wrapped line becomes its
        # own paragraph and a two-claim patent parses as fourteen.
        (
            "CLAIMS\n\n1. A widget comprising\n   a frobnicator.\n2. The widget of claim 1.\n",
            "hard wrapping",
        ),
        # Claims separated by a single newline rather than a blank one. Joining them
        # produces ONE paragraph holding every claim, which parses as no claims at all.
        ("CLAIMS\n1. A widget.\n2. A second widget.\n", "single-newline claims"),
        ("A\r\nB\r\n\r\n1. One.\r\n\r\n2. Two.\r\n", "windows line endings"),
        ("﻿A\n\n1. One.\n\n2. Two.\n", "byte order mark"),
        ("A\n\n1.\tOne.\n\n2.\tTwo.\n", "tabs"),
        ("A\n\n1. One.\x00\n\n2. Two.\n", "a stray NUL"),
        ("A\n\n1. One ​.\n\n2. Two.\n", "a zero-width space"),
        # Categories S and L, not C: a patent may legitimately quote either.
        ("CLAIMS\n\n1. A widget 🔬 with العربية and a < b.\n\n2. Second.\n", "emoji and RTL"),
        ("CLAIMS\n\n1. A <script>alert(1)</script> widget.\n\n2. Second.\n", "source markup"),
    ],
)
def test_messy_real_files_still_produce_two_claims_and_round_trip(raw: str, why: str) -> None:
    result = import_text(raw)
    assert result.claim_count == 2, why
    assert "\r" not in result.html and "\x00" not in result.html and "\t" not in result.html
    assert_round_trips(result)


def test_the_importer_reports_what_it_was_unsure_about_and_repairs_nothing() -> None:
    """Renumbering on import would rewrite a legal document nobody asked us to touch, and
    the cross-references would then point at the old numbers. So a duplicate, a gap and
    an out-of-order set are all imported exactly as written — and said out loud.

    A file with no claims heading gets one, because the parser needs either a heading or a
    run of numbered paragraphs; a lone numbered paragraph is a numbered sentence, not
    claim 1, and its digits survive rather than being silently destroyed.
    """
    duplicated = import_text("CLAIMS\n\n1. A widget.\n\n2. Second.\n\n2. Also.\n\n5. Fifth.\n")
    assert [c.number for c in parse(duplicated.html).claims] == [1, 2, 2, 5]
    assert any("2 appears more than once" in n for n in duplicated.notes)
    assert any("skips (2 to 5)" in n for n in duplicated.notes)
    assert_round_trips(duplicated)

    unordered = import_text("CLAIMS\n\n3. Third.\n\n1. First.\n\n2. Second.\n")
    assert any("not in ascending order" in n for n in unordered.notes)

    no_heading = import_text("Some Patent\n\n1. A widget.\n\n2. The widget of claim 1.\n")
    assert no_heading.claim_count == 2 and "<h1>Claims</h1>" in no_heading.html
    assert any('No "Claims" heading was found' in n for n in no_heading.notes)

    lone = import_text("Some Patent\n\nBACKGROUND\n\n1. Field of the Invention is widgets.\n")
    assert lone.claim_count == 0
    assert any("Only one numbered paragraph" in n for n in lone.notes)
    assert "1. Field of the Invention" in lone.html
    assert_round_trips(lone)

    shopping_list = import_text("milk\n\nbread\n\ncheese\n")
    assert parse(shopping_list.html).claims == []
    assert any("No numbered claims were recognised" in n for n in shopping_list.notes)
    assert "bread" in shopping_list.html

    empty = import_text("   \n\n  \n")
    assert empty.html == "" and empty.claim_count == 0
    assert any("no readable text" in n for n in empty.notes)


def test_a_separator_is_preserved_and_a_year_is_not_a_claim_number() -> None:
    """`1) 2) 3)` stays `1) 2) 3)`, because the separator is a field too. And the prefix
    pattern matches at most three digits so that "2024. In the prior art…" is a year: a
    document with a thousand claims does not exist, while one with a year in it does."""
    parens = import_text("CLAIMS\n\n1) A widget.\n\n2) The widget of claim 1.\n")
    assert [c.separator for c in parse(parens.html).claims] == [")", ")"]
    assert_round_trips(parens)

    big = import_text("CLAIMS\n\n1. First.\n\n2. Second.\n\n1000. Not a claim.\n")
    assert [c.number for c in parse(big.html).claims] == [1, 2]
    assert "1000. Not a claim." in big.html  # kept as text; nothing is destroyed
    assert_round_trips(big)


def test_a_one_megabyte_file_imports() -> None:
    """The save cap is 1,000,000 bytes, so this is the largest importable document."""
    filler = "comprising a frobnicator, a housing and a gasket of biocompatible material. " * 14
    claims = "\n\n".join(f"{n}. A widget of type {n} {filler}" for n in range(1, 901))
    raw = f"Big Patent\n\nCLAIMS\n\n{claims}\n"
    assert len(raw.encode()) > 900_000

    result = import_text(raw)
    assert result.claim_count == 900
    assert_round_trips(result)


def test_the_route_previews_writes_nothing_and_the_preview_is_saved_verbatim(
    client: TestClient,
) -> None:
    """The end of the journey, and the reason the conversion sanitises before returning:
    what the user previewed is what the database stores, byte for byte."""
    before = client.get("/api/documents").json()["total"]
    preview = client.post("/api/import/text", json={"text": SIMPLE, "filename": "widget.txt"})
    assert preview.status_code == status.HTTP_200_OK
    assert preview.json()["claim_count"] == 2
    assert preview.json()["title"] == "Widget With Improved Frobnicator"
    assert client.get("/api/documents").json()["total"] == before

    created = client.post(
        "/api/documents",
        json={"title": preview.json()["title"], "content": preview.json()["content"]},
    )
    assert created.status_code == status.HTTP_201_CREATED
    stored = client.get(f"/api/documents/{created.json()['id']}/versions/1").json()
    assert stored["content"] == preview.json()["content"]

    # A file that opens straight into its claims has no title line to take, so the
    # filename supplies one rather than "Imported patent".
    from_filename = client.post(
        "/api/import/text",
        json={
            "text": "1. A widget.\n\n2. The widget of claim 1.\n",
            "filename": "US_10123456_B2.txt",
        },
    )
    assert from_filename.json()["title"] == "US 10123456 B2"

    blank = client.post("/api/import/text", json={"text": "   \n"})
    assert blank.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert blank.json()["detail"] == EMPTY_FILE

    oversized = client.post("/api/import/text", json={"text": "x" * 1_000_001})
    assert oversized.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert "1000001" in oversized.json()["detail"]
