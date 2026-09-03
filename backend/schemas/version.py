"""Pydantic schemas for Version domain objects.

Mirrors :mod:`backend.db.models.versions.Version` — the release-version
container for a project's Epics and Bugs.  Field names, max lengths and
default values match the SQLAlchemy model exactly so that
``VersionRead.model_validate(version_orm_instance)`` round-trips cleanly.

Status values correspond to the ``ck_versions_status`` CHECK constraint
on the ``versions`` table (``planned | active | done | released``).  The ORM
column is a ``String`` type guarded by a DB-level CHECK rather than a
Python Enum, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.bug`,
:mod:`backend.schemas.epic` and :mod:`backend.schemas.project`.

In addition to mirrored columns, :class:`VersionRead` exposes three
aggregate counts — ``epic_count``, ``epics_done`` and ``bug_count`` —
that the service layer populates per DESIGN.md §2.6
``GET /projects/{id}/versions``.  They are plain integer fields with a
default of ``0`` rather than Pydantic ``computed_field`` properties
because they are sourced from SQL aggregate queries, not from attributes
already present on the ORM instance.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``status IN ('planned', 'active', 'done', 'released')``
# on the ``versions`` table.
#
# ``done`` (ICCINT-50, 03.09.2026): postavená, overená a Manažérom schválená verzia, ktorá ešte nebola
# nasadená. Bez neho hotová verzia niesla ``planned`` a na obrazovke sa nedala odlíšiť od nezačatej.
# Ponechať ju ``active`` až do nasadenia by ju zase nedalo odlíšiť od tej, ktorá sa práve stavia.
VersionStatus = Literal["planned", "active", "done", "released"]


class VersionCreate(BaseModel):
    """Payload for creating a new version.

    ``id``, ``status``, ``release_date``, ``created_at`` and
    ``updated_at`` are server-managed and therefore excluded:
    ``status`` defaults to ``planned`` via the DB-level
    ``server_default`` and transitions through ``active`` / ``released``
    via explicit endpoints (see DESIGN.md §2.6 ``POST
    /versions/{id}/release``); ``release_date`` is set by the service
    layer when the version is released.  ``project_id`` is supplied by
    the route path, not the body, so it is also excluded here.
    """

    version_number: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Semver-style version string, e.g. '1.0.0' or '1.1.0'.",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional human-readable release label, e.g. 'Pilot release'.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Release notes / scope description.",
    )
    target_date: Optional[date] = Field(
        default=None,
        description="Planned release date.",
    )


class VersionUpdate(BaseModel):
    """Partial update for an existing version.

    ``id``, ``project_id``, ``created_at`` and ``updated_at`` are
    immutable: the version identity and audit columns must not be
    rewritten after the fact.  ``release_date`` is typically set by the
    service layer when ``status`` transitions to ``released`` but is
    exposed here for backfill / correction flows.  All fields are
    optional to support PATCH-style semantics.
    """

    version_number: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Updated semver-style version string.",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Updated human-readable release label.",
    )
    status: Optional[VersionStatus] = Field(
        default=None,
        description="Updated lifecycle status: planned | active | done | released.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated release notes / scope description.",
    )
    target_date: Optional[date] = Field(
        default=None,
        description="Updated planned release date.",
    )
    release_date: Optional[date] = Field(
        default=None,
        description="Updated actual release date.",
    )


class VersionRead(BaseModel):
    """Serialised representation of a version row.

    Mirrors every column on :class:`backend.db.models.versions.Version`
    and adds three service-populated aggregate counts
    (``epic_count``, ``epics_done``, ``bug_count``) used by DESIGN.md
    §2.6 ``GET /projects/{id}/versions``.  ``from_attributes=True``
    enables construction directly from an ORM instance via
    ``VersionRead.model_validate(obj)``; the aggregate counts must be
    supplied explicitly by the service layer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    version_number: str = Field(..., min_length=1, max_length=50)
    name: Optional[str] = Field(default=None, max_length=255)
    status: VersionStatus
    description: Optional[str] = None
    target_date: Optional[date] = None
    release_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime

    # Service-populated aggregate counts — see module docstring.
    epic_count: int = Field(
        default=0,
        ge=0,
        description="Number of Epics assigned to this version.",
    )
    epics_done: int = Field(
        default=0,
        ge=0,
        description="Number of Epics in this version with status='done'.",
    )
    bug_count: int = Field(
        default=0,
        ge=0,
        description="Number of Bugs assigned to this version.",
    )
