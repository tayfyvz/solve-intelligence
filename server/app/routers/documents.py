from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app import crud
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Document, DocumentVersion
from app.sanitize import sanitize_html
from app.schemas import DocumentDetail, DocumentSummary, VersionRead, VersionWrite

router = APIRouter(prefix="/api/documents", tags=["documents"])

# SQLite's INTEGER is 64-bit. Without an upper bound, a larger integer passes
# FastAPI's `int` validation and raises OverflowError inside the driver — a 500
# where the caller should get a 422.
MAX_ID = 2**63 - 1


def _document_or_404(db: Session, document_id: int) -> Document:
    document = crud.get_document(db, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Document {document_id} not found.")
    return document


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
    """The one gate both writes pass through, so a third write route cannot
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


@router.get("", response_model=list[DocumentSummary])
def list_documents(db: Session = Depends(get_db)) -> list[Document]:
    return list(crud.list_documents(db))


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
) -> Document:
    return _document_or_404(db, document_id)


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
    body: VersionWrite,
    document_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentVersion:
    document = _document_or_404(db, document_id)
    content = _clean_or_413(body.content, settings)
    try:
        return crud.create_version(db, document, content)
    except crud.VersionNumberConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another save created a new version at the same time. Please try again.",
        ) from None


@router.put("/{document_id}/versions/{version_number}", response_model=VersionRead)
def update_version(
    body: VersionWrite,
    document_id: int = Path(ge=1, le=MAX_ID),
    version_number: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentVersion:
    """Updates in place. A missing version is a 404 — never an upsert."""
    version = _version_or_404(db, document_id, version_number)
    return crud.update_version(db, version, _clean_or_413(body.content, settings))
