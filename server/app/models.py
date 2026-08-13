from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Document(Base):
    """A patent. Identity and title only — content lives on DocumentVersion."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
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
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="versions")
