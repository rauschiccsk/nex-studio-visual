"""Service layer for :class:`~backend.db.models.delegations.Delegation`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.18 Delegation, §1.7 ``delegations`` table,
§2.6 Tasks (feat-level delegation trigger), §3.1 ``DelegationPage`` /
``DelegationStatus`` and :mod:`backend.db.models.delegations.Delegation`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``task_id``, ``feat_id``, ``bug_fix_task_id`` and ``bug_id`` are the
      delegation's "parent" references — a delegation belongs to at most
      one work item for its lifetime, so these FKs are immutable on the
      service layer. All four use ``ON DELETE SET NULL`` at the DB level,
      so the delegation row survives deletion of the originating work
      item (the FK is silently NULL-ed). :class:`DelegationUpdate`
      deliberately omits these columns and the service's
      ``allowed_fields`` allow-list enforces that contract defensively.
    * ``cc_agent`` and ``prompt`` are immutable because the agent identity
      and the prompt injected into the agent together form the
      delegation's execution contract — rewriting either post-creation
      would invalidate every downstream artefact (``raw_output``,
      ``execution_logs``, ``guardian_reviews``).
    * ``cc_agent`` is constrained by the ``ck_delegations_cc_agent`` DB
      CHECK (``ubuntu_cc``). The Pydantic
      :data:`~backend.schemas.delegation.DelegationCCAgent` literal
      mirrors the DB constraint.
    * ``status`` is constrained by the ``ck_delegations_status`` DB CHECK
      (``pending | running | done | failed``). The Pydantic
      :data:`~backend.schemas.delegation.DelegationStatus` literal
      mirrors the DB constraint, so the service does not revalidate — if
      an invalid value ever reaches the service (e.g. a bypassed schema)
      the DB CHECK rejects it on flush.
    * Inbound FKs on ``delegations`` — ``execution_logs.delegation_id``
      (``ON DELETE CASCADE``), ``guardian_reviews.delegation_id``
      (``ON DELETE CASCADE``) and ``auto_fix_attempts.delegation_id``
      (``ON DELETE SET NULL``) — are handled at the DB level, so
      :func:`delete` needs no RESTRICT dependency check. Dependent
      execution logs and guardian reviews are cascaded, and auto-fix
      attempts are silently NULL-ed out.
    * List filters (``task_id``, ``feat_id``, ``bug_fix_task_id``,
      ``bug_id``, ``status``, ``cc_agent``) match the indexed columns
      (``ix_delegations_status``, ``ix_delegations_started_at`` and the
      ``task_id`` index inherited from the FK declaration) and cover the
      natural lookup paths — "show every delegation for this task / feat
      / bug fix / bug", "show all running delegations", "show all
      delegations for the ubuntu agent".
    * List ordering is ``started_at DESC`` — the most recently started
      delegations appear first, matching the ``DelegationPage`` "active
      delegation + live output" convention (DESIGN.md §3.1) and the
      indexed column (``ix_delegations_started_at``).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.delegations import Delegation
from backend.schemas.delegation import (
    DelegationCCAgent,
    DelegationCreate,
    DelegationStatus,
    DelegationUpdate,
)


def list_delegations(
    db: Session,
    *,
    task_id: Optional[UUID] = None,
    feat_id: Optional[UUID] = None,
    bug_fix_task_id: Optional[UUID] = None,
    bug_id: Optional[UUID] = None,
    status: Optional[DelegationStatus] = None,
    cc_agent: Optional[DelegationCCAgent] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Delegation]:
    """Return delegations filtered by the supplied criteria.

    Results are ordered by ``started_at DESC`` so the most recently
    started delegations appear first — matching the
    ``DelegationPage`` "active delegation + live output" convention
    documented in DESIGN.md §3.1 and the indexed column
    (``ix_delegations_started_at``).

    Args:
        db: Active SQLAlchemy session.
        task_id: Optional task filter — restrict to delegations for a
            specific task (regular task-level delegation).
        feat_id: Optional feat filter — restrict to delegations for a
            specific feat (feat-level delegation trigger, DESIGN.md §2.6).
        bug_fix_task_id: Optional bug-fix-task filter — restrict to
            delegations spawned for a specific bug fix task.
        bug_id: Optional bug filter — restrict to delegations
            addressing a specific bug directly.
        status: Optional lifecycle-status filter (``pending`` |
            ``running`` | ``done`` | ``failed``).
        cc_agent: Optional CC agent filter (``ubuntu_cc``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`Delegation` instances.
    """
    stmt = select(Delegation)
    if task_id is not None:
        stmt = stmt.where(Delegation.task_id == task_id)
    if feat_id is not None:
        stmt = stmt.where(Delegation.feat_id == feat_id)
    if bug_fix_task_id is not None:
        stmt = stmt.where(Delegation.bug_fix_task_id == bug_fix_task_id)
    if bug_id is not None:
        stmt = stmt.where(Delegation.bug_id == bug_id)
    if status is not None:
        stmt = stmt.where(Delegation.status == status)
    if cc_agent is not None:
        stmt = stmt.where(Delegation.cc_agent == cc_agent)
    stmt = stmt.order_by(Delegation.started_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, delegation_id: UUID) -> Delegation:
    """Return a single delegation by primary key.

    Raises:
        ValueError: If no delegation with the supplied ``delegation_id``
            exists. The router converts this to an HTTP 404 response.
    """
    delegation = db.get(Delegation, delegation_id)
    if delegation is None:
        raise ValueError(f"Delegation {delegation_id} not found")
    return delegation


def create(db: Session, data: DelegationCreate) -> Delegation:
    """Create a new delegation.

    ``cc_agent``, ``status`` and ``started_at`` default to the DB-level
    ``server_default`` values (``ubuntu_cc``, ``pending`` and
    ``func.now()`` respectively) via the Pydantic schema when omitted.
    Nullable columns default to ``None``.

    Exactly which of ``task_id``, ``feat_id``, ``bug_fix_task_id`` or
    ``bug_id`` is populated is a caller decision — a delegation is
    linked to at most one of these (per DESIGN.md §1.18), and all four
    use ``ON DELETE SET NULL`` at the DB level so the delegation record
    survives deletion of the originating work item. The service does
    not enforce the "exactly one parent" invariant because the DB
    schema permits any combination (all four nullable), and admin /
    ad-hoc delegations legitimately have no parent at all. Callers that
    require the invariant should enforce it at the router / schema
    layer.

    If any supplied FK (``task_id``, ``feat_id``, ``bug_fix_task_id``,
    ``bug_id``) does not match an existing row the DB-level FK rejects
    the flush and the error propagates as-is (routed at the API layer
    as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Delegation` with its
        server-generated ``id``, ``created_at``, ``updated_at`` and
        (when omitted by the caller) ``started_at`` populated.
    """
    delegation = Delegation(
        task_id=data.task_id,
        feat_id=data.feat_id,
        bug_fix_task_id=data.bug_fix_task_id,
        bug_id=data.bug_id,
        cc_agent=data.cc_agent,
        prompt=data.prompt,
        status=data.status,
        raw_output=data.raw_output,
        commit_hash=data.commit_hash,
        started_at=data.started_at,
        completed_at=data.completed_at,
    )
    db.add(delegation)
    db.flush()
    return delegation


def update(db: Session, delegation_id: UUID, data: DelegationUpdate) -> Delegation:
    """Partially update a delegation.

    Only ``status``, ``raw_output``, ``commit_hash``, ``started_at`` and
    ``completed_at`` may be changed — these are the lifecycle fields
    stamped as the delegation progresses (pending → running → done /
    failed). ``id``, ``task_id``, ``feat_id``, ``bug_fix_task_id``,
    ``bug_id``, ``cc_agent``, ``prompt`` and ``created_at`` are
    immutable: the delegation identity, the agent contract and the
    prompt injected into the agent must not be rewritten after the
    fact; ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. Consequently, the
    explicit-null transitions ``raw_output -> NULL``,
    ``commit_hash -> NULL``, ``started_at -> NULL`` and
    ``completed_at -> NULL`` are not expressible through this service;
    those are rare corrections that belong to admin tooling rather
    than the UI.

    Raises:
        ValueError: If the delegation does not exist.
    """
    delegation = get_by_id(db, delegation_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable and
    # server-managed fields, but silently dropping any that slip
    # through keeps the service honest.
    allowed_fields = {
        "status",
        "raw_output",
        "commit_hash",
        "started_at",
        "completed_at",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(delegation, field, value)

    db.flush()
    return delegation


def delete(db: Session, delegation_id: UUID) -> None:
    """Hard-delete a delegation.

    Inbound FKs — ``execution_logs.delegation_id``
    (``ON DELETE CASCADE``), ``guardian_reviews.delegation_id``
    (``ON DELETE CASCADE``) and ``auto_fix_attempts.delegation_id``
    (``ON DELETE SET NULL``) — are handled at the DB level, so
    dependent execution logs and guardian reviews are cascaded and
    auto-fix attempts are silently NULL-ed out on flush. No RESTRICT
    dependency check is required. Deletion is reserved for test
    fixtures / admin tooling; routine operation retains the full
    delegation history for reporting (DESIGN.md §1.7).

    Raises:
        ValueError: If the delegation does not exist.
    """
    delegation = get_by_id(db, delegation_id)
    db.delete(delegation)
    db.flush()
