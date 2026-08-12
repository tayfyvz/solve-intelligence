from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VersionSummary(BaseModel):
    """A row in the version dropdown."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    updated_at: datetime


class DocumentSummary(BaseModel):
    """A row in the patent list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str


class DocumentDetail(BaseModel):
    """Metadata and version numbers — never content."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    versions: list[VersionSummary]


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: int
    version_number: int
    content: str
    updated_at: datetime


class VersionWrite(BaseModel):
    """Body for both POST (create) and PUT (update in place) — both buttons send
    the current editor HTML, and one body type is that symmetry in the schema.

    Empty content is valid: a user may legitimately clear a draft. There is no
    max_length either — the size cap is a router check returning 413 with a
    sentence, not a 422 carrying a validation-error array.
    """

    content: str
