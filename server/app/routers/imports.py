"""`POST /api/import/text` — turn an uploaded `.txt` into the HTML this app stores.

**This route writes nothing.** It has no `db` parameter, for the same reason the two AI
routes have none: there is then no code path through it that can reach the database. It
converts and reports, and the client sends the result to the existing
`POST /api/documents` or `POST /api/documents/{id}/versions` — so import gets version
creation, naming, sanitising, size limits and 409 handling for free rather than growing a
second copy of each.

It is a preview in the literal sense: `content` is byte-for-byte what a save will store,
because the conversion sanitises and canonicalises before returning. What the user reads
in the dialog is what lands in the editor.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.schemas import TextImportRequest, TextImportResult
from app.textimport import import_text

router = APIRouter(prefix="/api/import", tags=["import"])

EMPTY_FILE = "That file has no text in it, so there is nothing to import."
FILE_TOO_LARGE = "That file is too large to import ({size} bytes). The maximum is {cap} bytes."


def _title_from_filename(filename: str | None) -> str:
    """A last resort, used only when the text supplies no plausible title of its own.

    Trimmed of its extension and of the separators a downloaded file collects, so
    `US_10123456_B2.txt` opens as "US 10123456 B2" rather than as "Imported patent".
    """
    stem = (filename or "").rsplit("/", 1)[-1].removesuffix(".txt").removesuffix(".TXT")
    cleaned = " ".join(stem.replace("_", " ").replace("-", " ").split())
    return cleaned or "Imported patent"


@router.post("/text", response_model=TextImportResult)
def import_text_file(
    body: TextImportRequest,
    settings: Settings = Depends(get_settings),
) -> TextImportResult:
    """Convert only. No `db` parameter, so nothing here can persist anything."""
    # Bytes, not characters, and the same cap as the save path: a file that converts here
    # and then 413s on the way to the database would be a preview of something the user
    # can never keep. The check runs before any parsing, so a 50 MB paste is cheap.
    size = len(body.text.encode("utf-8"))
    if size > settings.max_content_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            FILE_TOO_LARGE.format(size=size, cap=settings.max_content_bytes),
        )
    if not body.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, EMPTY_FILE)

    result = import_text(body.text, fallback_title=_title_from_filename(body.filename))
    return TextImportResult(
        title=result.title,
        content=result.html,
        claim_count=result.claim_count,
        notes=result.notes,
    )
