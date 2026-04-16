"""Pydantic schemas for ModuleDependency domain objects.

Mirrors :mod:`backend.db.models.projects.ModuleDependency`.  Field names
and types match the SQLAlchemy model exactly so that
``ModuleDependencyRead.model_validate(orm_instance)`` round-trips
cleanly.

The natural key of the table is ``(module_id, depends_on_module_id)`` —
enforced by the
``uq_module_dependencies_module_id_depends_on_module_id`` UNIQUE
constraint.  Both foreign keys are immutable: a dependency edge in the
module DAG is a join row that is either created or deleted, never
rewritten in place (changing either endpoint would produce a different
edge).  Therefore :class:`ModuleDependencyUpdate` exposes no mutable
fields — it exists only for symmetry with the rest of the schema
package.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModuleDependencyCreate(BaseModel):
    """Payload for creating a new module dependency edge.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  Both ``module_id`` and ``depends_on_module_id``
    are required and together form the natural key of the row.

    Application-level cycle detection (DESIGN.md §1.2 ``module_dependencies``)
    runs before insert — the schema only enforces shape, not graph
    semantics.
    """

    module_id: UUID = Field(
        ...,
        description="The dependent module — the one that requires the other to be done first.",
    )
    depends_on_module_id: UUID = Field(
        ...,
        description=(
            "The prerequisite module — must have status='done' before the dependent module can enter 'in_development'."
        ),
    )


class ModuleDependencyUpdate(BaseModel):
    """Partial update for an existing module dependency edge.

    ``id``, ``created_at`` and ``updated_at`` are immutable.  The natural
    key ``(module_id, depends_on_module_id)`` is also immutable — a
    dependency edge is either created or deleted, never rewritten in
    place.  No mutable fields are therefore exposed; the schema exists
    for symmetry with the rest of the codebase.
    """


class ModuleDependencyRead(BaseModel):
    """Serialised representation of a module dependency row.

    Mirrors every column on
    :class:`backend.db.models.projects.ModuleDependency`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ModuleDependencyRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_id: UUID
    depends_on_module_id: UUID
    created_at: datetime
    updated_at: datetime
