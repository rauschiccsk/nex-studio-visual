"""Pydantic schemas for Bug domain objects.

Mirrors :mod:`backend.db.models.bugs.Bug`.  Field names, max lengths and
default values match the SQLAlchemy model exactly so that
``BugRead.model_validate(bug_orm_instance)`` round-trips cleanly.

Severity, status and source values correspond to the
``ck_bugs_severity``, ``ck_bugs_status`` and ``ck_bugs_source`` CHECK
constraints on the ``bugs`` table (``critical | major | minor``,
``new | accepted | in_progress | resolved | wont_fix`` and
``internal | customer`` respectively).  The ORM columns are ``String``
types guarded by DB-level CHECKs rather than Python Enums, so
``Literal`` is the narrowest faithful representation — consistent with
the approach used in :mod:`backend.schemas.guardian`,
:mod:`backend.schemas.user` and :mod:`backend.schemas.project`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `severity IN ('critical', 'major', 'minor')`
# on the ``bugs`` table.
BugSeverity = Literal["critical", "major", "minor"]

# Mirrors the CHECK constraint
# `status IN ('new', 'accepted', 'in_progress', 'resolved', 'wont_fix')`
# on the ``bugs`` table.
BugStatus = Literal["new", "accepted", "in_progress", "resolved", "wont_fix"]

# Mirrors the CHECK constraint `source IN ('internal', 'customer')`
# on the ``bugs`` table.
BugSource = Literal["internal", "customer"]


class BugCreate(BaseModel):
    """Payload for creating a new bug.

    ``id``, ``bug_number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``bug_number`` is
    auto-assigned as ``max(bug_number) + 1`` per project by the service
    layer.  ``status`` and ``source`` default to the values set by the
    DB-level ``server_default`` so callers may omit them.  Nullable
    columns default to ``None``.
    """

    project_id: UUID = Field(
        ...,
        description="Project the bug is reported against.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short bug title.",
    )
    description: str = Field(
        ...,
        description="Steps to reproduce, expected vs actual behaviour.",
    )
    severity: BugSeverity = Field(
        ...,
        description="Bug severity: critical | major | minor.",
    )
    status: BugStatus = Field(
        default="new",
        description="Lifecycle status: new | accepted | in_progress | resolved | wont_fix.",
    )
    source: BugSource = Field(
        default="internal",
        description="Where the bug originated: internal | customer.",
    )
    reported_by: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Customer name or internal user name who reported the bug.",
    )
    environment: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Environment where the bug was observed, e.g. 'production'.",
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the bug was resolved; set when status→resolved.",
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Commit that resolved the bug.",
    )
    created_by: UUID = Field(
        ...,
        description="User who registered the bug.",
    )


class BugUpdate(BaseModel):
    """Partial update for an existing bug.

    ``id``, ``project_id``, ``bug_number``, ``created_by`` and
    ``created_at`` are immutable: the bug identity and audit columns
    must not be rewritten after the fact.  ``updated_at`` is managed by
    the ORM via ``onupdate=func.now()`` and must not be set by clients.
    All remaining fields are optional to support PATCH-style semantics.
    ``resolved_at`` is typically set automatically by the service layer
    when ``status`` transitions to ``resolved`` but is exposed here for
    backfill / correction flows.
    """

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated bug title.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated bug description.",
    )
    severity: Optional[BugSeverity] = Field(
        default=None,
        description="Updated severity: critical | major | minor.",
    )
    status: Optional[BugStatus] = Field(
        default=None,
        description="Updated status: new | accepted | in_progress | resolved | wont_fix.",
    )
    source: Optional[BugSource] = Field(
        default=None,
        description="Updated source: internal | customer.",
    )
    reported_by: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Updated reporter name.",
    )
    environment: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Updated environment.",
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        description="Updated resolved_at timestamp.",
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Updated commit hash that resolved the bug.",
    )


class BugRead(BaseModel):
    """Serialised representation of a bug row.

    Mirrors every column on :class:`backend.db.models.bugs.Bug`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``BugRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    bug_number: int
    title: str = Field(..., min_length=1, max_length=500)
    description: str
    severity: BugSeverity
    status: BugStatus
    source: BugSource
    reported_by: Optional[str] = Field(default=None, max_length=255)
    environment: Optional[str] = Field(default=None, max_length=50)
    resolved_at: Optional[datetime] = None
    commit_hash: Optional[str] = Field(default=None, max_length=40)
    created_by: UUID
    created_at: datetime
    updated_at: datetime
