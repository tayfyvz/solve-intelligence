"""Data access only. No HTTPException, no Request — "not found" is None, a name
collision is a module-local exception, and the router turns those into statuses."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.data import SEED_DOCUMENTS
from app.models import Document, DocumentVersion


class VersionNumberConflict(Exception):
    """Concurrent saves kept claiming the same version number. The router turns
    this into a 409 — this layer raises no HTTPException."""


class NameTaken(Exception):
    """A unique-name index rejected the write. Raised only when the caller chose
    the name; auto-generated names retry instead (see `create_version`).

    `args[0]` is the name that lost, so the router can name it in the 409.
    """


# Neither document response carries version content, but `lazy="selectin"` would
# still fetch every draft's full body whenever a Document is loaded. Deferred
# here rather than on the column itself: a column-level defer also leaves
# `content` unloaded on the PUT path, where assigning to an unloaded attribute
# marks it dirty unconditionally and quietly changes what `update_version` below
# is doing.
_WITHOUT_CONTENT = selectinload(Document.versions).defer(DocumentVersion.content)

# One retry per competing writer we expect in practice (two or three browser tabs).
CREATE_VERSION_ATTEMPTS = 3

# The patent list needs three aggregates over a patent's versions. Computed in
# SQL and selected as plain columns: loading the versions to count them in Python
# would pull every draft body across for a list of titles.
_DOCUMENT_ROWS: Select[Any] = (
    select(
        Document.id,
        Document.title,
        Document.created_at,
        func.count(DocumentVersion.id).label("version_count"),
        func.coalesce(func.max(DocumentVersion.version_number), 0).label("latest_version_number"),
        # A patent's "last touched" is its most recently saved version. Coalesced
        # so a document with no versions still reports a time rather than null.
        func.coalesce(func.max(DocumentVersion.updated_at), Document.created_at).label(
            "updated_at"
        ),
    )
    .outerjoin(DocumentVersion, DocumentVersion.document_id == Document.id)
    .group_by(Document.id)
)


def list_documents(db: Session, limit: int, offset: int) -> tuple[Sequence[Row[Any]], int]:
    """One page of patents, plus the unfiltered total.

    Ordered by title, then id: titles are unique, but the id tiebreak makes the
    ordering total by construction, so a page can never repeat or skip a row.
    """
    total = db.scalar(select(func.count()).select_from(Document)) or 0
    rows = db.execute(
        _DOCUMENT_ROWS.order_by(Document.title, Document.id).limit(limit).offset(offset)
    ).all()
    return rows, total


def get_document_row(db: Session, document_id: int) -> Row[Any] | None:
    """The read model for GET/POST/PATCH /documents — aggregates, no content."""
    return db.execute(_DOCUMENT_ROWS.where(Document.id == document_id)).first()


def get_document(db: Session, document_id: int) -> Document | None:
    """The ORM entity, for the write paths that need one."""
    return db.get(Document, document_id, options=[_WITHOUT_CONTENT])


def list_versions(
    db: Session, document_id: int, limit: int, offset: int
) -> tuple[Sequence[Row[Any]], int]:
    """One page of a patent's versions, newest first. Selects columns rather than
    entities so no draft body is loaded to render a dropdown."""
    total = (
        db.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
        )
        or 0
    )
    rows = db.execute(
        select(
            DocumentVersion.version_number,
            DocumentVersion.name,
            DocumentVersion.source,
            DocumentVersion.created_at,
            DocumentVersion.updated_at,
        )
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total


def get_version(db: Session, document_id: int, version_number: int) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )


def count_versions(db: Session, document_id: int) -> int:
    """How many versions a document has. The router's only use is the "never
    zero versions" guard, so this is a count, not a fetch of the rows."""
    return (
        db.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
        )
        or 0
    )


def max_version_number(db: Session, document_id: int) -> int:
    """0 when the document has no versions."""
    return db.scalar(
        select(func.coalesce(func.max(DocumentVersion.version_number), 0)).where(
            DocumentVersion.document_id == document_id
        )
    )


# `func.lower` on both sides, never Python's str.lower: these comparisons must
# mean exactly what the unique expression indexes in models.py mean, and SQLite's
# lower() is ASCII-only where Python's is not.
def title_taken(db: Session, title: str, *, exclude_document_id: int | None = None) -> bool:
    """`exclude_document_id` lets a patent keep its own title on rename."""
    query = select(Document.id).where(func.lower(Document.title) == func.lower(title))
    if exclude_document_id is not None:
        query = query.where(Document.id != exclude_document_id)
    return db.scalar(query.limit(1)) is not None


def version_name_taken(
    db: Session, document_id: int, name: str, *, exclude_version_number: int | None = None
) -> bool:
    query = select(DocumentVersion.id).where(
        DocumentVersion.document_id == document_id,
        func.lower(DocumentVersion.name) == func.lower(name),
    )
    if exclude_version_number is not None:
        query = query.where(DocumentVersion.version_number != exclude_version_number)
    return db.scalar(query.limit(1)) is not None


def _auto_version_name(db: Session, document_id: int, version_number: int) -> str:
    """Returns `Version 3`, or `Version 3 (2)`, `(3)`... if a user took that name.

    A default name must never be able to fail the request, so it walks until it
    finds a free one. The walk is bounded by the number of existing versions.
    """
    base = f"Version {version_number}"
    candidate = base
    suffix = 1
    while version_name_taken(db, document_id, candidate):
        suffix += 1
        candidate = f"{base} ({suffix})"
    return candidate


def create_document(db: Session, title: str, content: str) -> Document:
    """Creates the patent and its first version in one transaction: a patent with
    no versions has nothing to open, so a half-done create is worse than none.

    Raises NameTaken if the title lost a race with a concurrent create.
    """
    document = Document(title=title)
    document.versions.append(DocumentVersion(version_number=1, name="Version 1", content=content))
    db.add(document)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NameTaken(title) from None
    db.refresh(document)
    return document


def rename_document(db: Session, document: Document, title: str) -> Document:
    document.title = title
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NameTaken(title) from None
    db.refresh(document)
    return document


def create_version(
    db: Session,
    document: Document,
    content: str,
    name: str | None = None,
    source: str = "user",
) -> DocumentVersion:
    """Reading MAX+1 and then inserting is a race: two tabs saving at the same
    moment compute the same number and the unique constraint rejects one of them.
    Retrying recomputes MAX+1 against the row the winner just committed, so the
    loser gets the next number instead of an unhandled IntegrityError.

    Raises VersionNumberConflict if every attempt loses, or NameTaken if an
    explicitly supplied name lost.
    """
    for _ in range(CREATE_VERSION_ATTEMPTS):
        number = max_version_number(db, document.id) + 1
        version = DocumentVersion(
            document_id=document.id,
            version_number=number,
            name=name if name is not None else _auto_version_name(db, document.id, number),
            content=content,
            source=source,
        )
        db.add(version)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # Two unique indexes can reject this insert. Asking the database which
            # one is more honest than parsing the driver's message — and an
            # auto-generated name is never the answer, because the next attempt
            # picks a fresh free one.
            if name is not None and version_name_taken(db, document.id, name):
                raise NameTaken(name) from None
            continue
        db.refresh(version)
        return version

    raise VersionNumberConflict


def update_version(db: Session, version: DocumentVersion, content: str) -> DocumentVersion:
    """Updates in place. Never creates a version, never renames — that is
    challenge task 1.3."""
    version.content = content
    # Saving unchanged content is still a save. Without this the attribute is not
    # dirty, SQLAlchemy emits no UPDATE, `onupdate` never fires, and the "last
    # saved" time in the version dropdown silently stays stale.
    version.updated_at = func.now()
    db.commit()
    db.refresh(version)
    return version


def delete_version(db: Session, version: DocumentVersion) -> None:
    """Deletes one version. The caller must already have refused deleting a
    document's last remaining version — this layer does not re-check, so it is
    never called with a document about to be left at zero.

    Other versions keep their own `version_number`: it is a stable identifier,
    not a positional index, so nothing here renumbers what remains.
    """
    db.delete(version)
    db.commit()


def rename_version(db: Session, version: DocumentVersion, name: str) -> DocumentVersion:
    """Renames only. Content is not touched, so `updated_at` moving here means
    "the label changed", which is still a change to the version."""
    version.name = name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NameTaken(name) from None
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
        document.versions.append(
            DocumentVersion(version_number=1, name="Version 1", content=seed.content)
        )
        db.add(document)
    db.commit()
    return len(SEED_DOCUMENTS)
