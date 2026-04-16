"""Pydantic schemas for Guardian domain objects.

Covers both :class:`GuardianPrecedent` — the allowlist / precedent
decisions attached to Guardian review findings — and
:class:`GuardianReview` — the Layer 1/2/3 review result for a
delegation.

Field names mirror :mod:`backend.db.models.guardian` exactly.  The
verdict, layer and risk-level values match the
``ck_guardian_precedents_verdict``, ``ck_guardian_reviews_layer`` and
``ck_guardian_reviews_risk_level`` CHECK constraints on the underlying
tables (``allow | notice | block``,
``layer1 | layer2 | layer3`` and
``low | medium | high | critical`` respectively).  The ORM columns are
``String`` types guarded by DB-level CHECKs rather than Python Enums,
so ``Literal`` is the narrowest faithful representation — consistent
with the approach used throughout :mod:`backend.schemas`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `verdict IN ('allow', 'notice', 'block')` on
# the `guardian_precedents` table.  The underlying ORM column is a
# ``String(10)`` guarded by a DB-level CHECK rather than a Python Enum, so a
# ``Literal`` is the narrowest faithful representation.
GuardianVerdict = Literal["allow", "notice", "block"]

# Mirrors the CHECK constraint `layer IN ('layer1', 'layer2', 'layer3')`
# on the `guardian_reviews` table.
GuardianReviewLayer = Literal["layer1", "layer2", "layer3"]

# Mirrors the CHECK constraint
# `risk_level IN ('low', 'medium', 'high', 'critical')` on the
# `guardian_reviews` table.
GuardianReviewRiskLevel = Literal["low", "medium", "high", "critical"]


class GuardianPrecedentCreate(BaseModel):
    """Payload for creating a new Guardian precedent.

    ``id`` and ``created_at`` are server-generated and therefore excluded.
    ``created_by`` is optional because the FK to ``users.id`` is nullable —
    legacy precedents (system-seeded) may have no human approver.
    """

    pattern_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="SHA-256 hex digest of 'rule:file:message[:50]'.",
    )
    pattern_description: str = Field(
        ...,
        min_length=1,
        description="Human-readable description of the precedent pattern.",
    )
    verdict: GuardianVerdict = Field(
        ...,
        description="Guardian action: allow (pass), notice (warn), block (fail).",
    )
    created_by: Optional[UUID] = Field(
        default=None,
        description="User who approved this precedent; NULL for system-seeded entries.",
    )


class GuardianPrecedentUpdate(BaseModel):
    """Partial update for an existing Guardian precedent.

    ``id``, ``pattern_hash``, ``created_by`` and ``created_at`` are immutable:
    the hash is a content-addressed identifier, and the audit columns must
    not be rewritten after the fact.  Only the human-readable description
    and the verdict can be amended.
    """

    pattern_description: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated human-readable description.",
    )
    verdict: Optional[GuardianVerdict] = Field(
        default=None,
        description="Updated verdict: allow | notice | block.",
    )


class GuardianPrecedentRead(BaseModel):
    """Serialised representation of a Guardian precedent row.

    Mirrors every column on :class:`backend.db.models.guardian.GuardianPrecedent`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``GuardianPrecedentRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pattern_hash: str = Field(..., min_length=64, max_length=64)
    pattern_description: str
    verdict: GuardianVerdict
    created_by: Optional[UUID] = None
    created_at: datetime


class GuardianReviewCreate(BaseModel):
    """Payload for creating a new Guardian review.

    ``id`` and ``created_at`` are server-generated and therefore
    excluded.  ``delegation_id``, ``layer`` and ``risk_level`` are
    required — a review is meaningless without its parent delegation,
    the pipeline layer that produced it and the maximum risk level of
    the changed files.  ``findings`` and ``passed`` default to the
    values set by the DB-level ``server_default`` (``[]`` and
    ``False``) so callers may omit them; ``duration_ms`` is nullable
    on the model and therefore optional here.
    """

    delegation_id: UUID = Field(
        ...,
        description=(
            "Parent delegation this review belongs to. The review is deleted "
            "when the delegation is deleted (``ON DELETE CASCADE``)."
        ),
    )
    layer: GuardianReviewLayer = Field(
        ...,
        description="Guardian pipeline layer that produced the review: layer1 | layer2 | layer3.",
    )
    risk_level: GuardianReviewRiskLevel = Field(
        ...,
        description="Maximum risk level of the changed files: low | medium | high | critical.",
    )
    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "JSONB array of finding objects (severity, rule, file_path, "
            "line_range, description, suggestion, confidence). Defaults to "
            "an empty list mirroring the ``server_default='[]'`` on the column."
        ),
    )
    passed: bool = Field(
        default=False,
        description=(
            "``True`` when no blocking issues were found. Defaults to ``False`` "
            "mirroring the ``server_default='false'`` on the column."
        ),
    )
    duration_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Wall-clock execution time of the review in milliseconds.",
    )


class GuardianReviewUpdate(BaseModel):
    """Partial update for an existing Guardian review.

    ``id``, ``delegation_id``, ``layer`` and ``created_at`` are
    immutable: the review identity and its parent delegation must not
    be rewritten after the fact, and the pipeline layer is fixed at
    creation time (a review for ``layer1`` cannot become a ``layer2``
    review).  Per DESIGN.md §1.21 reviews are conceptually immutable,
    but the remaining fields may still be amended to reflect
    post-hoc precedent filtering — for example, applying a new
    ``allow`` precedent may flip ``passed`` from ``False`` to ``True``
    and prune matched entries from ``findings``.  All fields are
    optional to support PATCH-style semantics.
    """

    risk_level: Optional[GuardianReviewRiskLevel] = Field(
        default=None,
        description="Updated maximum risk level: low | medium | high | critical.",
    )
    findings: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Updated JSONB array of finding objects after precedent filtering.",
    )
    passed: Optional[bool] = Field(
        default=None,
        description="Updated blocking flag; typically flipped to ``True`` after precedent filtering.",
    )
    duration_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated wall-clock execution time in milliseconds.",
    )


class GuardianReviewRead(BaseModel):
    """Serialised representation of a Guardian review row.

    Mirrors every column on :class:`backend.db.models.guardian.GuardianReview`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``GuardianReviewRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delegation_id: UUID
    layer: GuardianReviewLayer
    risk_level: GuardianReviewRiskLevel
    findings: list[dict[str, Any]]
    passed: bool
    duration_ms: Optional[int] = Field(default=None, ge=0)
    created_at: datetime
