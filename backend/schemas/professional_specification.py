"""Pydantic schemas for ProfessionalSpecification domain objects.

Mirrors :mod:`backend.db.models.specifications.ProfessionalSpecification`.
Field names, defaults and nullability match the SQLAlchemy model exactly
so that
``ProfessionalSpecificationRead.model_validate(orm_instance)``
round-trips cleanly.

A professional specification is the AI-generated, structured markdown
derived from a :class:`~backend.db.models.specifications.RawSpecification`
(see DESIGN.md §1.8 / §6.5).  It carries an ``approved_by`` /
``approved_at`` pair — once set (typically by an ``ri``-role user) the
document unlocks DESIGN.md generation (DESIGN.md §9 business rule and
§10 pipeline gating: ``professional_specifications.approved_by`` must be
non-null before ``design-documents/generate`` can be triggered).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProfessionalSpecificationCreate(BaseModel):
    """Payload for creating a new professional specification.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``version`` defaults to the value set by the
    DB-level ``server_default`` (``1``) so callers may omit it.
    Nullable approval columns default to ``None`` — approval happens as
    a later PATCH by an ``ri``-role user (consistent with the treatment
    of ``approved_by`` / ``approved_at`` on
    :class:`~backend.schemas.design_document.DesignDocumentCreate`).
    """

    raw_spec_id: UUID = Field(
        ...,
        description="Raw specification this professional specification was derived from.",
    )
    project_id: UUID = Field(
        ...,
        description=(
            "Project the specification belongs to. Denormalised from the raw specification "
            "for query convenience (DESIGN.md §6.5)."
        ),
    )
    content: str = Field(
        ...,
        min_length=1,
        description=(
            "Structured markdown content: business requirements, actors, use cases, constraints and out-of-scope items."
        ),
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Monotonic version number, incremented on each regeneration.",
    )
    approved_by: Optional[UUID] = Field(
        default=None,
        description="User (``ri`` role) who approved the specification. ``None`` = not yet approved.",
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the specification was approved.",
    )


class ProfessionalSpecificationUpdate(BaseModel):
    """Partial update for an existing professional specification.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` and ``raw_spec_id`` are immutable foreign
    keys — a professional specification belongs to exactly one project
    and is derived from exactly one raw specification for its lifetime,
    consistent with the treatment of ``project_id`` on
    :class:`~backend.schemas.design_document.DesignDocumentUpdate` and
    ``project_id`` / ``created_by`` on
    :class:`~backend.schemas.raw_specification.RawSpecificationUpdate`.
    All remaining fields are optional to support PATCH-style semantics.
    """

    content: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated structured markdown content of the professional specification.",
    )
    version: Optional[int] = Field(
        default=None,
        ge=1,
        description="Updated version number (typically incremented on regeneration).",
    )
    approved_by: Optional[UUID] = Field(
        default=None,
        description="Updated approver (``ri``-role user) for the specification.",
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        description="Updated approval timestamp.",
    )


class ProfessionalSpecificationRead(BaseModel):
    """Serialised representation of a professional specification row.

    Mirrors every column on
    :class:`backend.db.models.specifications.ProfessionalSpecification`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ProfessionalSpecificationRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_spec_id: UUID
    project_id: UUID
    content: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
