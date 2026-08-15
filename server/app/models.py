from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Names are compared case-insensitively after trimming, so the unique indexes at the foot
# of this file are expression indexes on `lower(name)` rather than plain constraints: a
# plain one accepts "Widget" alongside "widget", which is precisely the collision users
# mean. They are the backstop, not the fix — the routers pre-check and return a readable
# 409; these turn a lost race into an IntegrityError instead of a duplicate.
MAX_TITLE_LENGTH = 200
MAX_VERSION_NAME_LENGTH = 100


class Document(Base):
    """A patent. Identity and title only — content lives on DocumentVersion."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    # selectin: avoids N+1 on GET /api/documents, and avoids DetachedInstanceError
    # when the session closes before serialisation.
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number",
        lazy="selectin",
    )


class DocumentVersion(Base):
    """A mutable draft. Saving updates this row in place; deliberately NOT an
    immutable snapshot — that is the only model satisfying README task 1.3."""

    __tablename__ = "document_versions"
    # One row per (document, version). This is the last line of defence, not the
    # fix: it turns a concurrent POST into an IntegrityError rather than a
    # duplicate number, and `crud.create_version` is what retries and, failing
    # that, reports a 409.
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    # What the user calls this draft. Defaults to "Version {n}" on create, but it
    # is a plain label: nothing derives version identity from it.
    name: Mapped[str] = mapped_column(String(MAX_VERSION_NAME_LENGTH), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Who created this version: "user" (a plain save or import) or "ai" (the confirmed
    # result of an AI-proposed edit). Set once at creation and never changed, so it
    # answers "who made this version exist", not "who last touched it". The client reads
    # it to decide whether further AI edits here may apply in place.
    source: Mapped[str] = mapped_column(String(4), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="versions")


# Declared out here rather than in __table_args__ because func.lower() needs the
# mapped attribute, which does not exist until the class body has run.
Index("uq_document_title_lower", func.lower(Document.title), unique=True)
Index(
    "uq_document_version_name_lower",
    DocumentVersion.document_id,
    func.lower(DocumentVersion.name),
    unique=True,
)
