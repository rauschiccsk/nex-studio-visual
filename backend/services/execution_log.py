"""Service layer for :class:`~backend.db.models.delegations.ExecutionLog`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.19 ExecutionLog, §1.7 ``execution_logs``
table and :mod:`backend.db.models.delegations.ExecutionLog`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``delegation_id`` and ``task_id`` are the log's "parent"
      references — an execution log belongs to exactly one delegation
      (and optionally one task) for its lifetime, so both FKs are
      immutable on the service layer. ``delegation_id`` uses
      ``ON DELETE CASCADE`` at the DB level and ``task_id`` uses
      ``ON DELETE SET NULL``, so cleanup happens automatically when the
      parent row is dropped. :class:`ExecutionLogUpdate` deliberately
      omits these columns and the service's ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``status`` is constrained by the ``ck_execution_logs_status`` DB
      CHECK (``done | failed``). The Pydantic
      :data:`~backend.schemas.execution_log.ExecutionLogStatus` literal
      mirrors the DB constraint, so the service does not revalidate —
      if an invalid value ever reaches the service (e.g. a bypassed
      schema) the DB CHECK rejects it on flush.
    * ``commit_verified`` defaults to ``False`` via the DB-level
      ``server_default`` and is flipped to ``True`` only after the
      GitHub-API verification job confirms the reported
      ``commit_hash`` exists on the target branch (DESIGN.md §1.7,
      "Commit verification").
    * ``execution_logs`` has no inbound FKs, so :func:`delete` needs
      no RESTRICT dependency check — simply drop the row. Deletion is
      reserved for test fixtures / admin tooling; routine operation
      retains the full execution history for reporting.
    * List filters (``delegation_id``, ``task_id``, ``status``,
      ``commit_verified``) match the indexed columns
      (``ix_execution_logs_delegation_id``,
      ``ix_execution_logs_task_id``) and cover the natural lookup
      paths — "show every log for this delegation", "show every log
      for this task", "show all failed executions", "show all
      unverified commits" (for the GitHub verification job).
    * List ordering is ``created_at DESC`` — the most recently
      recorded executions appear first, matching the reporting views
      which surface the latest activity at the top.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.delegations import ExecutionLog
from backend.schemas.execution_log import (
    ExecutionLogCreate,
    ExecutionLogStatus,
    ExecutionLogUpdate,
)


def list_execution_logs(
    db: Session,
    *,
    delegation_id: Optional[UUID] = None,
    task_id: Optional[UUID] = None,
    status: Optional[ExecutionLogStatus] = None,
    commit_verified: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ExecutionLog]:
    """Return execution logs filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    recorded executions appear first — matching the reporting
    conventions used throughout the UI.

    Args:
        db: Active SQLAlchemy session.
        delegation_id: Optional delegation filter — restrict to logs
            belonging to a specific delegation (the core
            delegation-scoped history query, DESIGN.md §1.19).
        task_id: Optional task filter — restrict to logs belonging to
            a specific task.
        status: Optional terminal-status filter (``done`` | ``failed``).
        commit_verified: Optional verification-flag filter — typically
            ``False`` to drive the GitHub verification job, ``True``
            to list already-verified commits for reporting.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ExecutionLog` instances.
    """
    stmt = select(ExecutionLog)
    if delegation_id is not None:
        stmt = stmt.where(ExecutionLog.delegation_id == delegation_id)
    if task_id is not None:
        stmt = stmt.where(ExecutionLog.task_id == task_id)
    if status is not None:
        stmt = stmt.where(ExecutionLog.status == status)
    if commit_verified is not None:
        stmt = stmt.where(ExecutionLog.commit_verified == commit_verified)
    stmt = stmt.order_by(ExecutionLog.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, execution_log_id: UUID) -> ExecutionLog:
    """Return a single execution log by primary key.

    Raises:
        ValueError: If no execution log with the supplied
            ``execution_log_id`` exists. The router converts this to
            an HTTP 404 response.
    """
    log = db.get(ExecutionLog, execution_log_id)
    if log is None:
        raise ValueError(f"ExecutionLog {execution_log_id} not found")
    return log


def create(db: Session, data: ExecutionLogCreate) -> ExecutionLog:
    """Create a new execution log.

    ``commit_verified`` defaults to ``False`` via the Pydantic schema
    (mirroring the DB ``server_default='false'``) when omitted; the
    flag is flipped to ``True`` only after GitHub API verification
    (DESIGN.md §1.7 "Commit verification"). All other optional fields
    default to ``None`` when omitted.

    If the supplied ``delegation_id`` or ``task_id`` foreign key does
    not match an existing row the DB-level FK rejects the flush and
    the error propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ExecutionLog` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.
    """
    log = ExecutionLog(
        delegation_id=data.delegation_id,
        task_id=data.task_id,
        status=data.status,
        duration_seconds=data.duration_seconds,
        input_tokens=data.input_tokens,
        output_tokens=data.output_tokens,
        total_cost_usd=data.total_cost_usd,
        commit_hash=data.commit_hash,
        commit_verified=data.commit_verified,
    )
    db.add(log)
    db.flush()
    return log


def update(
    db: Session,
    execution_log_id: UUID,
    data: ExecutionLogUpdate,
) -> ExecutionLog:
    """Partially update an execution log.

    Only ``status``, ``duration_seconds``, ``input_tokens``,
    ``output_tokens``, ``total_cost_usd``, ``commit_hash`` and
    ``commit_verified`` may be changed. ``id``, ``delegation_id``,
    ``task_id`` and ``created_at`` are immutable: the log identity and
    its parent references must not be rewritten after the fact (the
    DB handles orphaning via ``ON DELETE CASCADE`` / ``ON DELETE SET
    NULL`` automatically); ``updated_at`` is auto-stamped by the ORM
    on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. ``commit_verified`` is the
    notable exception — it is a ``bool`` (not ``Optional[bool]``) in
    effect: the schema permits ``None`` to mean "omit" and any
    explicit ``True`` / ``False`` is applied. Consequently, the
    explicit-null transitions on the nullable metrics columns
    (``duration_seconds -> NULL``, ``input_tokens -> NULL``,
    ``output_tokens -> NULL``, ``total_cost_usd -> NULL``,
    ``commit_hash -> NULL``) are not expressible through this
    service; those are rare corrections that belong to admin tooling
    rather than the UI.

    Raises:
        ValueError: If the execution log does not exist.
    """
    log = get_by_id(db, execution_log_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable and
    # server-managed fields, but silently dropping any that slip
    # through keeps the service honest.
    allowed_fields = {
        "status",
        "duration_seconds",
        "input_tokens",
        "output_tokens",
        "total_cost_usd",
        "commit_hash",
        "commit_verified",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(log, field, value)

    db.flush()
    return log


def delete(db: Session, execution_log_id: UUID) -> None:
    """Hard-delete an execution log.

    ``execution_logs`` has no inbound FKs, so no RESTRICT dependency
    check is required — simply drop the row. Deletion is reserved for
    test fixtures / admin tooling; routine operation retains the full
    execution history for reporting (DESIGN.md §1.19).

    Raises:
        ValueError: If the execution log does not exist.
    """
    log = get_by_id(db, execution_log_id)
    db.delete(log)
    db.flush()
