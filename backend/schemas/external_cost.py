"""Pydantic schemas for :class:`~backend.db.models.external_cost.ExternalCost` (CR-V2-063).

Hand-entered token spend the cockpit cannot meter. The declarative constraints here (non-empty
``description``/``model``, ``≤ 500``/``≤ 100`` chars, non-negative token counts) are the 422 gate for
everything decidable from the payload alone; the two checks that need the DB — ``version_id`` must
belong to THIS project, ``occurred_on`` must not be in the future — live in the router (also 422).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExternalCostCreate(BaseModel):
    """Payload for creating an entry. ``project_id`` comes from the route path and ``created_by`` from
    the authenticated user — neither is client-supplied."""

    occurred_on: date = Field(..., description="Deň, kedy práca prebehla (nesmie byť v budúcnosti).")
    description: str = Field(..., min_length=1, max_length=500, description="Čo sa robilo.")
    model: str = Field(..., min_length=1, max_length=100, description='Úplné id modelu, napr. "claude-opus-5".')
    input_tokens: int = Field(default=0, ge=0, description="Spotrebované vstupné tokeny.")
    output_tokens: int = Field(default=0, ge=0, description="Spotrebované výstupné tokeny.")
    version_id: Optional[UUID] = Field(
        default=None,
        description="Verzia projektu; prázdne = celý projekt (do súčtu projektu, do žiadnej verzie).",
    )


class ExternalCostUpdate(BaseModel):
    """Partial update — only the supplied fields change. ``version_id=None`` explicitly moves an entry
    back to the project level (``model_fields_set`` distinguishes it from "not supplied")."""

    occurred_on: Optional[date] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=500)
    model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    version_id: Optional[UUID] = None


class ExternalCostRead(BaseModel):
    """Serialised entry. ``from_attributes`` enables ``model_validate(orm_obj)``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    version_id: Optional[UUID] = None
    occurred_on: date
    description: str
    model: str
    input_tokens: int
    output_tokens: int
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
