"""Service layer for :class:`~backend.db.models.tasks.Epic`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``epics`` table, §2.6 ``POST /projects/{id}/epics``, §4.0 Version
Lifecycle Rules, §6.6 list filters, §6.8 Service Layer Extension and
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
    * ``version_id`` is **required** at creation time (DESIGN.md §4.0
      Rule 2 — every new EPIC must be assigned to a release version
      before it can be scheduled). The underlying column is nullable at
      the DB level only so that the FK ``ON DELETE RESTRICT`` remains
      expressible for legacy rows; the service enforces the stronger
      application-level contract and rejects ``version_id is None``
      with :class:`ValueError` (HTTP 422 at the router layer).
    * ``status`` is constrained by the ``ck_epics_status`` DB CHECK
      (``planned | in_progress | done``). The Pydantic
      :data:`~backend.schemas.epic.EpicStatus` literal mirrors the DB
      constraint, so the service does not revalidate — if an invalid
      value ever reaches the service (e.g. a bypassed schema) the DB
      CHECK rejects it on flush.
    * :func:`update` wires in the DESIGN.md §4.0 Rule 4 auto-activate
      trigger: whenever an epic transitions **into** ``in_progress``
      and carries a ``version_id``, the linked version is promoted from
      ``planned`` → ``active`` via
      :func:`backend.services.version.auto_activate`. The helper is
      idempotent — already-``active`` / ``released`` versions are a
      no-op — so double-firing the trigger on repeated patches does
      nothing unsafe.
    * ``epics`` has a single inbound FK (``feats.epic_id``) with
      ``ON DELETE CASCADE`` — :func:`delete` therefore needs no
      RESTRICT dependency check; dependent feats (and the tasks under
      them, via ``tasks.feat_id ON DELETE CASCADE``) are removed
      automatically at the DB level.
    * List filters (``project_id``, ``status``) match
      the indexed columns (``ix_epics_project_id``)
      and support the Tasks UI (DESIGN.md §3.1
      ``TasksPage`` / ``EpicList``) and
      reporting (DESIGN.md §3.1 ``ReportsPage``) — "show every epic in
      this project", "show every in-progress epic".
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
from backend.services import version as version_service


def list_epics(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
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
    if status is not None:
        stmt = stmt.where(Epic.status == status)
    stmt = stmt.order_by(Epic.number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_epics(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    status: Optional[EpicStatus] = None,
) -> int:
    """Return the total number of epics matching the given filters.

    Mirrors the ``project_id`` / ``status`` filters of
    :func:`list_epics` so a paginated response can report the unfiltered
    total alongside the current page of items (same pattern as
    :func:`~backend.services.bug.count_bugs` and
    :func:`~backend.services.design_document.count_design_documents`).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter.
        status: Optional lifecycle-status filter (``planned`` |
            ``in_progress`` | ``done``).

    Returns:
        Total number of rows matching the filters.
    """
    stmt = select(func.count()).select_from(Epic)
    if project_id is not None:
        stmt = stmt.where(Epic.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Epic.status == status)
    return int(db.execute(stmt).scalar_one())


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

    ``version_id`` is **required** (DESIGN.md §4.0 Rule 2 — every new
    EPIC belongs to a release version). The service raises
    :class:`ValueError` when the caller passes ``None`` so the router
    can translate it to HTTP 422. The underlying column is nullable at
    the DB level only because ``ON DELETE RESTRICT`` must remain
    expressible for legacy rows; the application-level contract is
    stricter.

    ``status`` defaults to ``planned`` via the Pydantic schema / DB
    ``server_default`` when omitted, matching the model declaration.

    If the supplied ``project_id`` or ``version_id``
    foreign keys do not match existing rows the DB-level FK rejects the
    flush and the error propagates as-is (routed at the API layer as a
    409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Epic` with its
        server-generated ``id``, ``number``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If ``version_id`` is ``None`` (DESIGN.md §4.0
            Rule 2) or if another epic already uses the same
            ``(project_id, number)`` pair (concurrent-create race).
    """
    if data.version_id is None:
        raise ValueError("version_id required for new epics")

    number = _next_epic_number(db, data.project_id)
    if _get_by_project_and_number(db, data.project_id, number) is not None:
        raise ValueError(f"Epic with project_id={data.project_id} and number={number} already exists")

    epic = Epic(
        project_id=data.project_id,
        version_id=data.version_id,
        number=number,
        title=data.title,
        plain_description=data.plain_description,
        status=data.status,
    )
    db.add(epic)
    db.flush()
    return epic


def update(db: Session, epic_id: UUID, data: EpicUpdate) -> Epic:
    """Partially update an epic.

    Only ``title`` and ``status`` may be changed. ``id``,
    ``project_id``, ``number`` and ``created_at`` are immutable — an
    epic belongs to exactly one project for its lifetime, its position
    within the project (``number``) must not be rewritten after the
    fact, and ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics.

    Version lifecycle trigger (DESIGN.md §4.0 Rule 4): when ``status``
    transitions **into** ``in_progress`` and the epic carries a
    ``version_id``, the linked version is promoted from ``planned`` →
    ``active`` via
    :func:`backend.services.version.auto_activate`. The helper is a
    no-op for versions already in ``active`` / ``released`` (Rule 3 —
    status only flows forward), so firing it on every transition into
    ``in_progress`` is safe even for repeated PATCHes.

    Raises:
        ValueError: If the epic does not exist.
    """
    epic = get_by_id(db, epic_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "title",
        "status",
    }

    # Capture the pre-update status so we can detect the exact
    # ``-> in_progress`` transition and avoid firing ``auto_activate``
    # on idempotent re-patches that keep ``status = 'in_progress'``.
    previous_status = epic.status

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(epic, field, value)

    db.flush()

    new_status = update_data.get("status")
    if new_status == "in_progress" and previous_status != "in_progress" and epic.version_id is not None:
        version_service.auto_activate(db, epic.version_id)

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
