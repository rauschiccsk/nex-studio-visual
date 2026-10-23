"""Pydantic schemas for Feat domain objects.

Mirrors :mod:`backend.db.models.tasks.Feat`.  Field names, max lengths
and default values match the SQLAlchemy model exactly so that
a payload maps onto the model one to one.

Status values correspond to the ``ck_feats_status`` CHECK constraint on
the ``feats`` table (``todo | in_progress | done | failed``).  The ORM
column is a ``String`` type guarded by a DB-level CHECK rather than a
Python Enum, so ``Literal`` is the narrowest faithful representation —
consistent with :mod:`backend.schemas.epic`, :mod:`backend.schemas.project` and
:mod:`backend.schemas.user`.
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# Mirrors the CHECK constraint
# ``status IN ('todo', 'in_progress', 'done', 'failed')``
# on the ``feats`` table.
FeatStatus = Literal["todo", "in_progress", "done", "failed"]


class FeatCreate(BaseModel):
    """Payload for creating a new feat.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per epic by the service layer
    (DESIGN.md §2.6).  ``status`` and
    ``description`` default to the values set by the DB-level
    ``server_default`` (``todo`` and ``''`` respectively) so callers may
    omit them.  ``auto_fix_count`` is a server-managed counter and is not
    accepted here.  Nullable columns default to ``None``.
    """

    epic_id: UUID = Field(
        ...,
        description="Epic the feat belongs to.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short feat title.",
    )
    description: str = Field(
        default="",
        description="Detailed feat description. Defaults to an empty string.",
    )
    plain_description: Optional[str] = Field(
        default=None,
        description=(
            "Plain-language, jargon-free one-liner for the STEP 3 Plán úloh rail (step3-plan-design.md) — "
            "distinct from the technical ``description``. Nullable; ``None`` when the AI partner emitted none."
        ),
    )
    status: FeatStatus = Field(
        default="todo",
        description="Lifecycle status: todo | in_progress | done | failed.",
    )
