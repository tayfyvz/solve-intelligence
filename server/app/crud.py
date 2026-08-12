"""Data access only. No HTTPException, no Request — "not found" is None, and the
router turns that into a 404."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import SEED_DOCUMENTS
from app.models import Document, DocumentVersion


def list_documents(db: Session) -> Sequence[Document]:
    return db.scalars(select(Document).order_by(Document.id)).all()


def get_document(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def get_version(db: Session, document_id: int, version_number: int) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )


def max_version_number(db: Session, document_id: int) -> int:
    """0 when the document has no versions."""
    return db.scalar(
        select(func.coalesce(func.max(DocumentVersion.version_number), 0)).where(
            DocumentVersion.document_id == document_id
        )
    )


def create_version(db: Session, document: Document, content: str) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        version_number=max_version_number(db, document.id) + 1,
        content=content,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def update_version(db: Session, version: DocumentVersion, content: str) -> DocumentVersion:
    """Updates in place. Never creates a version — that is challenge task 1.3."""
    version.content = content
    # Saving unchanged content is still a save. Without this the attribute is not
    # dirty, SQLAlchemy emits no UPDATE, `onupdate` never fires, and the "last
    # saved" time in the version dropdown silently stays stale.
    version.updated_at = func.now()
    db.commit()
    db.refresh(version)
    return version


def seed_if_empty(db: Session) -> int:
    """Insert the seed patents if the table is empty. Returns documents inserted.

    The guard is a count, not hardcoded ids: with a file-backed database, an
    unconditional `insert(id=1)` raises IntegrityError on the second boot and the
    app never starts.

    Deliberately does not sanitise: a test asserts the seed survives the
    sanitiser unchanged, so a broken allowlist fails loudly instead of quietly
    mangling the seed at boot.
    """
    if db.scalar(select(func.count()).select_from(Document)):
        return 0

    for seed in SEED_DOCUMENTS:
        document = Document(title=seed.title)
        document.versions.append(DocumentVersion(version_number=1, content=seed.content))
        db.add(document)
    db.commit()
    return len(SEED_DOCUMENTS)
