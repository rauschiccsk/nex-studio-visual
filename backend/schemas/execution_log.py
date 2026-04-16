"""Pydantic schemas for ExecutionLog domain objects.

Mirrors :mod:`backend.db.models.delegations.ExecutionLog`.  Field names,
max lengths and default values match the SQLAlchemy model exactly so
that ``ExecutionLogRead.model_validate(execution_log_orm_instance)``
round-trips cleanly.

Status values correspond to the ``ck_execution_logs_status`` CHECK
constraint on the ``execution_logs`` table (``done | failed``).  The
ORM column is a ``String`` type guarded by a DB-level CHECK rather
than a Python Enum, so ``Literal`` is the narrowest faithful
representation — consistent with the approach used in
:mod:`backend.schemas.delegation`,
:mod:`backend.schemas.architect_message`,
:mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.bug_fix_task`,
:mod:`backend.schemas.feat`,
:mod:`backend.schemas.epic`,
:mod:`backend.schemas.bug`,
:mod:`backend.schemas.project_module`,
:mod:`backend.schemas.project` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, condecimal

# Mirrors the CHECK constraint ``status IN ('done', 'failed')`` on the
# ``execution_logs`` table.
ExecutionLogStatus = Literal["done", "failed"]

# Mirrors `total_cost_usd DECIMAL(10, 6)` on the ``execution_logs``
# table.
ExecutionLogTotalCost = condecimal(max_digits=10, decimal_places=6)


class ExecutionLogCreate(BaseModel):
    """Payload for creating a new execution log entry.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``delegation_id`` and ``status`` are required —
    an execution log is meaningless without its parent delegation and
    terminal status.  ``commit_verified`` defaults to ``False`` (matches
    the ``server_default='false'`` on the column) so callers may omit
    it; the value is flipped to ``True`` only after GitHub API
    verification (see DESIGN.md §1.7, "Commit verification").  All
    remaining fields are nullable on the model and therefore optional
    on the schema.
    """

    delegation_id: UUID = Field(
        ...,
        description=(
            "Parent delegation this log belongs to. The execution log is deleted "
            "when the delegation is deleted (``ON DELETE CASCADE``)."
        ),
    )
    task_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional task this execution targeted. Set to NULL if the task is later deleted (``ON DELETE SET NULL``)."
        ),
    )
    status: ExecutionLogStatus = Field(
        ...,
        description="Terminal status of the execution: done | failed.",
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Wall-clock duration of the CC delegation in seconds.",
    )
    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Anthropic API input tokens consumed, extracted from the NDJSON ``result`` event.",
    )
    output_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Anthropic API output tokens produced, extracted from the NDJSON ``result`` event.",
    )
    total_cost_usd: Optional[ExecutionLogTotalCost] = Field(  # type: ignore[valid-type]
        default=None,
        description="Total USD cost for the delegation, derived from the token counts.",
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Git commit hash produced by the delegation, if any.",
    )
    commit_verified: bool = Field(
        default=False,
        description=(
            "Whether the reported ``commit_hash`` has been confirmed via the GitHub "
            "API. Defaults to ``False`` and is flipped to ``True`` only after "
            "successful verification."
        ),
    )


class ExecutionLogUpdate(BaseModel):
    """Partial update for an existing execution log.

    ``id`` and ``created_at`` are immutable: the log identity must not
    be rewritten after the fact.  ``updated_at`` is managed by the ORM
    via ``onupdate=func.now()`` and must not be set by clients.
    ``delegation_id`` and ``task_id`` are the log's parent references —
    an execution log belongs to exactly one delegation for its
    lifetime, so these FKs are immutable (the DB handles orphaning via
    ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` automatically).  All
    remaining fields are optional to support PATCH-style semantics —
    in particular, ``commit_verified`` is flipped from ``False`` to
    ``True`` by the GitHub-verification job after the log is first
    written.
    """

    status: Optional[ExecutionLogStatus] = Field(
        default=None,
        description="Updated terminal status: done | failed.",
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated wall-clock duration in seconds.",
    )
    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated Anthropic API input token count.",
    )
    output_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated Anthropic API output token count.",
    )
    total_cost_usd: Optional[ExecutionLogTotalCost] = Field(  # type: ignore[valid-type]
        default=None,
        description="Updated total USD cost.",
    )
    commit_hash: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Updated git commit hash produced by the delegation.",
    )
    commit_verified: Optional[bool] = Field(
        default=None,
        description="Updated verification flag; typically flipped to ``True`` after GitHub API verification.",
    )


class ExecutionLogRead(BaseModel):
    """Serialised representation of an execution log row.

    Mirrors every column on
    :class:`backend.db.models.delegations.ExecutionLog`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ExecutionLogRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delegation_id: UUID
    task_id: Optional[UUID] = None
    status: ExecutionLogStatus
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_cost_usd: Optional[Decimal] = None
    commit_hash: Optional[str] = Field(default=None, max_length=40)
    commit_verified: bool
    created_at: datetime
    updated_at: datetime
