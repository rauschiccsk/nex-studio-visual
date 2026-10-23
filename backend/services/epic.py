"""Service layer for :class:`~backend.db.models.tasks.Epic` — read one and create.

The build writes a version's plan through :func:`create` (the plan, a fast fix); every function takes ``db: Session``
first and only flushes — the caller commits. ``number`` is ``MAX(number) + 1`` per project, re-validated against
``uq_epics_project_id_number`` so a concurrent create surfaces as :class:`ValueError`, not a raw
:class:`~sqlalchemy.exc.IntegrityError`. ``version_id`` is required (DESIGN.md §4.0 Rule 2). DEV-155 (23.10.2026)
removed update, delete and the listings, which only the first version's API called.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Epic
from backend.schemas.epic import (
    EpicCreate,
)


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
