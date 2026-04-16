"""Service layer for :class:`~backend.db.models.tasks.Epic`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``epics`` table, §2.6 ``POST /projects/{id}/epics``, §6.6 list filters,
§6.8 Service Layer Extension and
:mod:`backend.db.models.tasks.Epic`):

    * ``id``, ``number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer
      (``updated_at`` is auto-stamped by the ORM via
      ``onupdate=func.now()`` on flush).
    * ``project_id`` is an immutable foreign key — an epic belongs to
      exactly one project for its lifetime. :class:`EpicUpdate`
      deliberately omits it and the service's ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``number`` is auto-assigned by :func:`create` as
      ``MAX(number) + 1`` for the supplied ``project_id`` (starts at
      ``1`` for the first epic in a project). The DB-level
      ``UNIQUE(project_id, number)`` constraint
      (``uq_epics_project_id_number``) is re-validated defensively
      before flush so concurrent creates on the same project — which
      are rare but possible — still surface as :class:`ValueError`
      instead of raw :class:`~sqlalchemy.exc.IntegrityError`.
    * ``status`` is constrained by the ``ck_epics_status`` DB CHECK
      (``planned | in_progress | done``). The Pydantic
      :data:`~backend.schemas.epic.EpicStatus` literal mirrors the DB
      constraint, so the service does not revalidate — if an invalid
      value ever reaches the service (e.g. a bypassed schema) the DB
      CHECK rejects it on flush.
    * ``module_id`` remains mutable: ``NULL`` denotes a project-level
      epic (used by single-module projects — see schema docstring and
      DESIGN.md §1.9) and the DB-level ``ON DELETE SET NULL`` naturally
      expresses the same transition when the referenced module is
      removed. In-place re-scoping of an existing epic is rare but
      expressible.
    * ``epics`` has a single inbound FK (``feats.epic_id``) with
      ``ON DELETE CASCADE`` — :func:`delete` therefore needs no
      RESTRICT dependency check; dependent feats (and the tasks under
      them, via ``tasks.feat_id ON DELETE CASCADE``) are removed
      automatically at the DB level.
    * List filters (``project_id``, ``module_id``, ``status``) match
      the indexed columns (``ix_epics_project_id``,
      ``ix_epics_module_id``) and support the Tasks UI (DESIGN.md §3.1
      ``TasksPage`` / ``EpicList`` with "filterable by module") and
      reporting (DESIGN.md §3.1 ``ReportsPage``) — "show every epic in
      this project", "show every epic scoped to this module", "show
      every in-progress epic".
    * List ordering is ``number ASC`` — epics display in creation
      order (epic 1, epic 2, …) to match the hierarchical-numbering
      convention described in DESIGN.md §1.9 and the ``EpicList``
      collapsible UI. Within a project the ``number`` column is
      monotonically increasing, so ordering by ``number`` gives a
      stable, human-readable sequence that aligns with the user-facing
      identifiers.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Epic
from backend.schemas.epic import (
    EpicCreate,
    EpicStatus,
    EpicUpdate,
)


def list_epics(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    module_id: Optional[UUID] = None,
    status: Optional[EpicStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Epic]:
    """Return epics filtered by the supplied criteria.

    Results are ordered by ``number ASC`` so epics appear in their
    stable, human-readable numbering order (epic 1, epic 2, …) — this
    matches the hierarchical-numbering convention documented in
    DESIGN.md §1.9 and the ``EpicList`` UI (DESIGN.md §3.1).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to epics
            belonging to a specific project (the core Tasks-page query,
            DESIGN.md §3.1 ``TasksPage``).
        module_id: Optional module filter — restrict to epics scoped
            to a specific module. Pass the module UUID to fetch
            module-scoped epics; project-level epics (``module_id IS
            NULL``) are filtered out when this argument is supplied.
        status: Optional lifecycle-status filter (``planned`` |
            ``in_progress`` | ``done``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`Epic` instances.
    """
    stmt = select(Epic)
    if project_id is not None:
        stmt = stmt.where(Epic.project_id == project_id)
    if module_id is not None:
        stmt = stmt.where(Epic.module_id == module_id)
    if status is not None:
        stmt = stmt.where(Epic.status == status)
    stmt = stmt.order_by(Epic.number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, epic_id: UUID) -> Epic:
    """Return a single epic by primary key.

    Raises:
        ValueError: If no epic with the supplied ``epic_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    epic = db.get(Epic, epic_id)
    if epic is None:
        raise ValueError(f"Epic {epic_id} not found")
    return epic


def _next_epic_number(db: Session, project_id: UUID) -> int:
    """Return the next ``number`` to assign within a project.

    Scans ``MAX(number)`` for the supplied ``project_id`` and returns
    ``max + 1`` (or ``1`` when the project has no epics yet). The
    DB-level ``UNIQUE(project_id, number)`` constraint is the ultimate
    guard against concurrent duplicates — the service also re-checks
    the pair before flush (see :func:`_get_by_project_and_number`).
    """
    stmt = select(func.max(Epic.number)).where(Epic.project_id == project_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_project_and_number(
    db: Session,
    project_id: UUID,
    number: int,
) -> Optional[Epic]:
    """Internal helper — look up an epic by the ``(project_id, number)`` pair."""
    stmt = select(Epic).where(
        Epic.project_id == project_id,
        Epic.number == number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: EpicCreate) -> Epic:
    """Create a new epic.

    Auto-assigns ``number`` as ``MAX(number) + 1`` for the supplied
    ``project_id``. The computed pair is re-validated against the DB
    unique constraint before flush so a race between concurrent creates
    on the same project surfaces as a clean :class:`ValueError` (HTTP
    409 at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``status`` defaults to ``planned`` via the Pydantic schema / DB
    ``server_default`` when omitted, matching the model declaration.
    ``module_id`` may be ``None`` to register a project-level epic (used
    by single-module projects).

    If the supplied ``project_id`` or ``module_id`` foreign keys do not
    match existing rows the DB-level FK rejects the flush and the error
    propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Epic` with its
        server-generated ``id``, ``number``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If another epic already uses the same
            ``(project_id, number)`` pair (concurrent-create race).
    """
    number = _next_epic_number(db, data.project_id)
    if _get_by_project_and_number(db, data.project_id, number) is not None:
        raise ValueError(f"Epic with project_id={data.project_id} and number={number} already exists")

    epic = Epic(
        project_id=data.project_id,
        module_id=data.module_id,
        number=number,
        title=data.title,
        status=data.status,
    )
    db.add(epic)
    db.flush()
    return epic


def update(db: Session, epic_id: UUID, data: EpicUpdate) -> Epic:
    """Partially update an epic.

    Only ``module_id``, ``title`` and ``status`` may be changed. ``id``,
    ``project_id``, ``number`` and ``created_at`` are immutable — an
    epic belongs to exactly one project for its lifetime, its position
    within the project (``number``) must not be rewritten after the
    fact, and ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — ``module_id`` is therefore
    sticky once set. The explicit-null "downgrade to project-level"
    transition is not expressible through this service; it is a rare
    correction that belongs to admin tooling rather than the UI.

    Raises:
        ValueError: If the epic does not exist.
    """
    epic = get_by_id(db, epic_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "module_id",
        "title",
        "status",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(epic, field, value)

    db.flush()
    return epic


def delete(db: Session, epic_id: UUID) -> None:
    """Hard-delete an epic.

    The single inbound FK (``feats.epic_id``) uses ``ON DELETE
    CASCADE`` — dependent feats (and the tasks under them, via
    ``tasks.feat_id ON DELETE CASCADE``) are removed automatically at
    the DB level. No RESTRICT dependency check is required.

    Raises:
        ValueError: If the epic does not exist.
    """
    epic = get_by_id(db, epic_id)
    db.delete(epic)
    db.flush()
