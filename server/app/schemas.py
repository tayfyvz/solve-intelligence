from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import MAX_TITLE_LENGTH, MAX_VERSION_NAME_LENGTH

# strip_whitespace runs before the length checks, so "   " is a 422 for being
# empty rather than being stored as a name made of spaces. Names are stored in
# exactly the trimmed form validated here.
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH),
]
VersionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_VERSION_NAME_LENGTH),
]


class VersionSummary(BaseModel):
    """A row in the version dropdown."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    name: str
    created_at: datetime
    updated_at: datetime


class DocumentSummary(BaseModel):
    """A row in the patent list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    version_count: int
    updated_at: datetime


class DocumentDetail(BaseModel):
    """Metadata and counts — never content, and never the version list: that is
    its own paginated endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    version_count: int
    latest_version_number: int
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    """`total` is the unfiltered count, so the client can render page counts.

    Spelled out per collection rather than as a generic Page[T]: a pydantic
    generic serialises into OpenAPI as `Page_DocumentSummary_`, which is not a
    name anyone would hand-write on the client.
    """

    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class VersionPage(BaseModel):
    items: list[VersionSummary]
    total: int
    limit: int
    offset: int


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: int
    version_number: int
    name: str
    content: str
    created_at: datetime
    updated_at: datetime


class DocumentCreate(BaseModel):
    """`content` omitted means an empty first version — a new patent starts
    blank, and "" is a legitimate document body."""

    title: Title
    content: str | None = None


class DocumentRename(BaseModel):
    title: Title


class VersionCreate(BaseModel):
    """Body for POST. `name` omitted means "Version {number}".

    Empty content is valid: a user may legitimately clear a draft. There is no
    max_length on content either — the size cap is a router check returning 413
    with a sentence, not a 422 carrying a validation-error array.
    """

    content: str
    name: VersionName | None = None


class VersionUpdate(BaseModel):
    """Body for PUT (update in place). Content only: PUT never renames."""

    content: str


class VersionRename(BaseModel):
    """Body for PATCH. Name only: PATCH never touches content."""

    name: VersionName
