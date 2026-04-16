"""Pydantic schemas for Delegation domain objects.

Mirrors :mod:`backend.db.models.delegations.Delegation`.  Field names,
max lengths and default values match the SQLAlchemy model exactly so
that ``DelegationRead.model_validate(delegation_orm_instance)``
round-trips cleanly.

CC-agent and status values correspond to the
``ck_delegations_cc_agent`` and ``ck_delegations_status`` CHECK
constraints on the ``delegations`` table (``ubuntu_cc`` and
``pending | running | done | failed`` respectively).  The ORM columns
are ``String`` types guarded by DB-level CHECKs rather than Python
Enums, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in
:mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.auto_fix_attempt`,
:mod:`backend.schemas.bug_fix_task`,
:mod:`backend.schemas.feat`,
:mod:`backend.schemas.epic`,
:mod:`backend.schemas.bug`,
:mod:`backend.schemas.project_module`,
:mod:`backend.schemas.project` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint ``cc_agent IN ('ubuntu_cc')`` on the
# ``delegations`` table.
DelegationCCAgent = Literal["ubuntu_cc"]

# Mirrors the CHECK constraint
# ``status IN ('pending', 'running', 'done', 'failed')`` on the
# ``delegations`` table.
DelegationStatus = Literal["pending", "running", "done", "failed"]


class DelegationCreate(BaseModel):
    """Payload for creating a new CC delegation.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``cc_agent``, ``status`` and ``started_at``
    default to the values set by the DB-level ``server_default``
    (``ubuntu_cc``, ``pending`` and ``func.now()`` respectively) so
    callers may omit them.  Nullable columns default to ``None``.

    Exactly which of ``task_id``, ``feat_id``, ``bug_fix_task_id`` or
    ``bug_id`` is populated is determined by the caller — a delegation
    is linked to at most one of these, all of which are
    ``ON DELETE SET NULL`` at the DB level so the delegation record
    survives deletion of the originating work item (see
    :class:`backend.db.models.delegations.Delegation`).
    """

    task_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional task this delegation executes. Set to NULL if the task is later deleted (``ON DELETE SET NULL``)."
        ),
    )
    feat_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional feat this delegation executes (feat-level delegation). Set to NULL if the feat is later deleted."
        ),
    )
    bug_fix_task_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional bug fix task this delegation executes. Set to NULL if the bug fix task is later deleted."
        ),
    )
    bug_id: Optional[UUID] = Field(
        default=None,
        description=("Optional bug this delegation addresses directly. Set to NULL if the bug is later deleted."),
    )
    cc_agent: DelegationCCAgent = Field(
        default="ubuntu_cc",
        description="CC agent that will execute the delegation.",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        description="Full CC delegation prompt injected into the agent.",
    )
    status: DelegationStatus = Field(
        default="pending",
        description="Lifecycle status: pending | running | done | failed.",
    )
    raw_output: Optional[str] = Field(
        default=None,
        description=(
            "Raw NDJSON / text stream captured from the CC agent; typically populated as the delegation progresses."
        ),
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Git commit hash produced by the delegation, if any.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description=(
            "When the CC agent began executing. Defaults to ``NOW()`` via the DB-level server default if omitted."
        ),
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "When the CC agent finished executing; ``None`` while the delegation is still pending or running."
        ),
    )


class DelegationUpdate(BaseModel):
    """Partial update for an existing delegation.

    ``id`` and ``created_at`` are immutable: the delegation identity
    must not be rewritten after the fact.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``task_id``, ``feat_id``, ``bug_fix_task_id`` and
    ``bug_id`` are the delegation's "parent" references — a delegation
    belongs to at most one work item for its lifetime, so these FKs
    are immutable (the DB handles orphaning via ``ON DELETE SET NULL``
    automatically).  ``cc_agent`` and ``prompt`` are immutable because
    the agent identity and injected prompt define the delegation's
    execution contract.  All remaining lifecycle fields are optional
    to support PATCH-style semantics as the delegation progresses.
    """

    status: Optional[DelegationStatus] = Field(
        default=None,
        description="Updated lifecycle status: pending | running | done | failed.",
    )
    raw_output: Optional[str] = Field(
        default=None,
        description="Updated raw NDJSON / text stream captured from the CC agent.",
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Updated git commit hash produced by the delegation.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="Updated execution start timestamp.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Updated execution completion timestamp.",
    )


class DelegationRead(BaseModel):
    """Serialised representation of a delegation row.

    Mirrors every column on
    :class:`backend.db.models.delegations.Delegation`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``DelegationRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: Optional[UUID] = None
    feat_id: Optional[UUID] = None
    bug_fix_task_id: Optional[UUID] = None
    bug_id: Optional[UUID] = None
    cc_agent: DelegationCCAgent
    prompt: str = Field(..., min_length=1)
    status: DelegationStatus
    raw_output: Optional[str] = None
    commit_hash: Optional[str] = Field(default=None, max_length=40)
    started_at: datetime
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
