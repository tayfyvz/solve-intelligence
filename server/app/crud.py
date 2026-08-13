"""Data access only. No HTTPException, no Request — "not found" is None, and the
router turns that into a 404."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.data import SEED_DOCUMENTS
from app.models import Document, DocumentVersion


class VersionNumberConflict(Exception):
    """Concurrent saves kept claiming the same version number. The router turns
    this into a 409 — this layer raises no HTTPException."""


# Neither document response carries version content, but `lazy="selectin"` would
# still fetch every draft's full body to build the dropdown. Deferred here rather
# than on the column itself: a column-level defer also leaves `content` unloaded
# on the PUT path, where assigning to an unloaded attribute marks it dirty
# unconditionally and quietly changes what `update_version` below is doing.
_WITHOUT_CONTENT = selectinload(Document.versions).defer(DocumentVersion.content)

# One retry per competing writer we expect in practice (two or three browser tabs).
CREATE_VERSION_ATTEMPTS = 3


def list_documents(db: Session) -> Sequence[Document]:
    return db.scalars(select(Document).options(_WITHOUT_CONTENT).order_by(Document.id)).all()


def get_document(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id, options=[_WITHOUT_CONTENT])


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
    """Reading MAX+1 and then inserting is a race: two tabs saving at the same
    moment compute the same number and the unique constraint rejects one of them.
    Retrying recomputes MAX+1 against the row the winner just committed, so the
    loser gets the next number instead of an unhandled IntegrityError.

    Raises VersionNumberConflict if every attempt loses.
    """
    for _ in range(CREATE_VERSION_ATTEMPTS):
        version = DocumentVersion(
            document_id=document.id,
            version_number=max_version_number(db, document.id) + 1,
            content=content,
        )
        db.add(version)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(version)
        return version

    raise VersionNumberConflict


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
