from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import Row
from sqlalchemy.orm import Session

from app import crud
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Document, DocumentVersion
from app.sanitize import sanitize_html
from app.schemas import (
    DocumentCreate,
    DocumentDetail,
    DocumentPage,
    DocumentRename,
    VersionCreate,
    VersionPage,
    VersionRead,
    VersionRename,
    VersionUpdate,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])

# SQLite's INTEGER is 64-bit. Without an upper bound, a larger integer passes
# FastAPI's `int` validation and raises OverflowError inside the driver — a 500
# where the caller should get a 422.
MAX_ID = 2**63 - 1

# Paging bounds. The max is a guard on response size, not a preference: without
# it, `?limit=1000000` is a denial-of-service button.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _document_or_404(db: Session, document_id: int) -> Document:
    document = crud.get_document(db, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Document {document_id} not found.")
    return document


def _document_row_or_404(db: Session, document_id: int) -> Row[Any]:
    row = crud.get_document_row(db, document_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Document {document_id} not found.")
    return row


def _version_or_404(db: Session, document_id: int, version_number: int) -> DocumentVersion:
    # The document is checked first, so /documents/999/versions/1 says the
    # document is missing rather than blaming the version.
    _document_or_404(db, document_id)
    version = crud.get_version(db, document_id, version_number)
    if version is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Version {version_number} of document {document_id} not found.",
        )
    return version


def _clean_or_413(raw: str, settings: Settings) -> str:
    """The one gate every write passes through, so a fourth write route cannot
    forget it."""
    # Bytes, not characters: a 1M-character document is ~3 MB on the wire. The
    # check runs before sanitising, because nh3 on a 50 MB string is free CPU.
    size = len(raw.encode("utf-8"))
    cap = settings.max_content_bytes
    if size > cap:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Document content is too large ({size} bytes). The maximum is {cap} bytes.",
        )
    return sanitize_html(raw)


def _title_conflict(title: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, f'A patent called "{title}" already exists.')


def _version_name_conflict(name: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, f'Version "{name}" already exists in this patent.'
    )


@router.get("", response_model=DocumentPage)
def list_documents(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentPage:
    """An offset past the end is an empty page, not a 404: it is a stale link,
    and `total` tells the client where the data actually ends."""
    rows, total = crud.list_documents(db, limit, offset)
    return DocumentPage(items=list(rows), total=total, limit=limit, offset=offset)


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
def create_document(
    body: DocumentCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Row[Any]:
    """Creates the patent and its first version, named "Version 1"."""
    content = _clean_or_413(body.content or "", settings)
    # The pre-check is what produces a readable message; the unique index below
    # is what survives two creates racing. Both are needed.
    if crud.title_taken(db, body.title):
        raise _title_conflict(body.title)
    try:
        document = crud.create_document(db, body.title, content)
    except crud.NameTaken as taken:
        raise _title_conflict(taken.args[0]) from None
    return _document_row_or_404(db, document.id)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
) -> Row[Any]:
    return _document_row_or_404(db, document_id)


@router.patch("/{document_id}", response_model=DocumentDetail)
def rename_document(
    body: DocumentRename,
    document_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
) -> Row[Any]:
    """Renames only — a patent's content lives on its versions."""
    document = _document_or_404(db, document_id)
    # Excluding itself, so re-saving a patent's own title (or only its casing)
    # is a no-op rather than a conflict with itself.
    if crud.title_taken(db, body.title, exclude_document_id=document_id):
        raise _title_conflict(body.title)
    try:
        crud.rename_document(db, document, body.title)
    except crud.NameTaken as taken:
        raise _title_conflict(taken.args[0]) from None
    return _document_row_or_404(db, document_id)


@router.get("/{document_id}/versions", response_model=VersionPage)
def list_versions(
    document_id: int = Path(ge=1, le=MAX_ID),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> VersionPage:
    """Newest first, because that is the draft the user is most likely after."""
    _document_or_404(db, document_id)
    rows, total = crud.list_versions(db, document_id, limit, offset)
    return VersionPage(items=list(rows), total=total, limit=limit, offset=offset)


@router.get("/{document_id}/versions/{version_number}", response_model=VersionRead)
def get_version(
    document_id: int = Path(ge=1, le=MAX_ID),
    version_number: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    return _version_or_404(db, document_id, version_number)


@router.post(
    "/{document_id}/versions",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    body: VersionCreate,
    document_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentVersion:
    document = _document_or_404(db, document_id)
    content = _clean_or_413(body.content, settings)
    # Only an explicit name can conflict: an omitted one is generated from the
    # names already taken, so it cannot collide.
    if body.name is not None and crud.version_name_taken(db, document_id, body.name):
        raise _version_name_conflict(body.name)
    try:
        return crud.create_version(db, document, content, body.name)
    except crud.NameTaken as taken:
        raise _version_name_conflict(taken.args[0]) from None
    except crud.VersionNumberConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another save created a new version at the same time. Please try again.",
        ) from None


@router.put("/{document_id}/versions/{version_number}", response_model=VersionRead)
def update_version(
    body: VersionUpdate,
    document_id: int = Path(ge=1, le=MAX_ID),
    version_number: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentVersion:
    """Updates in place. A missing version is a 404 — never an upsert. The name
    is not in the body and cannot be changed here."""
    version = _version_or_404(db, document_id, version_number)
    return crud.update_version(db, version, _clean_or_413(body.content, settings))


@router.patch("/{document_id}/versions/{version_number}", response_model=VersionRead)
def rename_version(
    body: VersionRename,
    document_id: int = Path(ge=1, le=MAX_ID),
    version_number: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    """Renames only — content is never read from this body."""
    version = _version_or_404(db, document_id, version_number)
    if crud.version_name_taken(db, document_id, body.name, exclude_version_number=version_number):
        raise _version_name_conflict(body.name)
    try:
        return crud.rename_version(db, version, body.name)
    except crud.NameTaken as taken:
        raise _version_name_conflict(taken.args[0]) from None
