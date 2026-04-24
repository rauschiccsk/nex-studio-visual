"""Pydantic schemas for RawSpecification domain objects.

Mirrors :mod:`backend.db.models.specifications.RawSpecification`.  Field
names, max lengths and default values match the SQLAlchemy model exactly
so that ``RawSpecificationRead.model_validate(orm_instance)`` round-trips
cleanly.

Input-format values correspond to the
``ck_raw_specifications_input_format`` CHECK constraint on the
``raw_specifications`` table (``text | pdf | docx``) and status values
correspond to the ``ck_raw_specifications_status`` CHECK constraint
(``pending | processing | done | failed``).  Both ORM columns are
``String`` types guarded by DB-level CHECKs rather than Python Enums, so
``Literal`` is the narrowest faithful representation — consistent with
the approach used in :mod:`backend.schemas.architect_message`,
:mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.design_document`,
:mod:`backend.schemas.kb_document`,
:mod:`backend.schemas.migration_batch`,
:mod:`backend.schemas.project`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``input_format IN ('text', 'pdf', 'docx')`` on the
# ``raw_specifications`` table.
RawSpecificationInputFormat = Literal["text", "pdf", "docx"]

# Mirrors the CHECK constraint
# ``status IN ('pending', 'processing', 'done', 'failed')`` on the
# ``raw_specifications`` table.
RawSpecificationStatus = Literal["pending", "processing", "done", "failed"]


class RawSpecificationCreate(BaseModel):
    """Payload for creating a new raw customer specification.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``input_format``, ``language`` and ``status``
    default to the values set by the DB-level ``server_default``
    (``text``, ``sk`` and ``pending`` respectively) so callers may omit
    them.
    """

    project_id: UUID = Field(
        ...,
        description="Project the raw specification belongs to.",
    )
    input_text: str = Field(
        ...,
        min_length=1,
        description="Free-form customer specification text (verbatim from the customer).",
    )
    input_format: RawSpecificationInputFormat = Field(
        default="text",
        description="Original input format of the specification: text | pdf | docx.",
    )
    language: str = Field(
        default="sk",
        min_length=1,
        max_length=10,
        description="ISO-style language code of the specification (e.g. ``sk``, ``en``).",
    )
    status: RawSpecificationStatus = Field(
        default="pending",
        description="Processing status: pending | processing | done | failed.",
    )
    created_by: UUID = Field(
        ...,
        description="User who uploaded the raw specification.",
    )


class RawSpecificationUpdate(BaseModel):
    """Partial update for an existing raw customer specification.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` and ``created_by`` are immutable foreign
    keys — a raw specification belongs to exactly one project and one
    uploader for its lifetime, consistent with the treatment of
    ``project_id`` and ``created_by`` on
    :class:`~backend.schemas.architect_session.ArchitectSessionUpdate`.
    All remaining fields are optional to support PATCH-style semantics.
    """

    input_text: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated free-form customer specification text.",
    )
    input_format: Optional[RawSpecificationInputFormat] = Field(
        default=None,
        description="Updated original input format: text | pdf | docx.",
    )
    language: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Updated language code of the specification.",
    )
    status: Optional[RawSpecificationStatus] = Field(
        default=None,
        description="Updated processing status: pending | processing | done | failed.",
    )
    approved_by: Optional[UUID] = Field(
        default=None,
        description="User that approved the specification; cleared on un-approve.",
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        description="ISO-8601 timestamp when the specification was approved.",
    )


class RawSpecificationRead(BaseModel):
    """Serialised representation of a raw specification row.

    Mirrors every column on
    :class:`backend.db.models.specifications.RawSpecification`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``RawSpecificationRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    input_text: str = Field(..., min_length=1)
    input_format: RawSpecificationInputFormat
    language: str = Field(..., min_length=1, max_length=10)
    status: RawSpecificationStatus
    created_by: UUID
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
