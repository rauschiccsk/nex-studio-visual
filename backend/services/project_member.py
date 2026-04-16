"""Service layer for :class:`~backend.db.models.projects.ProjectMember`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.4 ProjectMember / §2.2 project_members
table and :mod:`backend.db.models.projects.ProjectMember`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``(project_id, user_id)`` is the natural key of the row —
      ``UNIQUE(project_id, user_id)`` (see
      ``uq_project_members_project_id_user_id``). Both columns are
      immutable: a membership is a join row that is either created or
      deleted, never rewritten in place (DESIGN.md §4.1 authorization
      rule: membership is a capability, not a state machine). The
      :class:`ProjectMemberUpdate` schema therefore exposes no mutable
      fields; the service's allow-list formalises that contract
      defensively.
    * Unique constraint on ``(project_id, user_id)`` is enforced both
      at the DB layer and pre-emptively by :func:`create`, so callers
      receive a clean :class:`ValueError` (HTTP 409 at the router
      layer) instead of a raw :class:`~sqlalchemy.exc.IntegrityError`
      coming out of ``flush``.
    * ``project_members`` has **no** inbound foreign keys — no other
      table references it — so :func:`delete` performs no dependency
      RESTRICT check. The outbound FKs are ``project_id`` (``ON DELETE
      CASCADE``) and ``user_id`` (``ON DELETE CASCADE``), so a
      project or user deletion cleans up the membership rows
      automatically.
    * List filters (``project_id``, ``user_id``) match the two
      indexed columns (``ix_project_members_project_id``,
      ``ix_project_members_user_id``) and support the two typical
      queries: "who belongs to this project" (settings / team UI) and
      "which projects does this user belong to" (dashboard
      visibility enforcement, DESIGN.md §4.1).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.projects import ProjectMember
from backend.schemas.project_member import (
    ProjectMemberCreate,
    ProjectMemberUpdate,
)


def list_project_members(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ProjectMember]:
    """Return project memberships filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    added members appear first, matching the settings / team-management
    UI convention (latest joiners on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to memberships
            belonging to a specific project (the "who belongs here"
            query).
        user_id: Optional user filter — restrict to memberships for a
            specific user (the "which projects can this user see"
            query, used by DESIGN.md §4.1 project-visibility
            enforcement).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ProjectMember` instances.
    """
    stmt = select(ProjectMember)
    if project_id is not None:
        stmt = stmt.where(ProjectMember.project_id == project_id)
    if user_id is not None:
        stmt = stmt.where(ProjectMember.user_id == user_id)
    stmt = stmt.order_by(ProjectMember.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, member_id: UUID) -> ProjectMember:
    """Return a single project membership by primary key.

    Raises:
        ValueError: If no membership with the supplied ``member_id``
            exists. The router converts this to an HTTP 404 response.
    """
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise ValueError(f"ProjectMember {member_id} not found")
    return member


def _get_by_natural_key(
    db: Session,
    project_id: UUID,
    user_id: UUID,
) -> Optional[ProjectMember]:
    """Internal helper — look up a membership by its ``(project_id, user_id)`` natural key."""
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: ProjectMemberCreate) -> ProjectMember:
    """Create a new project membership.

    Validates the ``UNIQUE(project_id, user_id)`` constraint before
    insertion so the caller receives a clean :class:`ValueError` (HTTP
    409 at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    If the supplied ``project_id`` or ``user_id`` does not match an
    existing row, the DB-level FK rejects the flush and the error
    propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ProjectMember` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If a membership linking the supplied ``project_id``
            and ``user_id`` already exists.
    """
    if _get_by_natural_key(db, data.project_id, data.user_id) is not None:
        raise ValueError(f"ProjectMember for project_id={data.project_id} user_id={data.user_id} already exists")

    member = ProjectMember(
        project_id=data.project_id,
        user_id=data.user_id,
    )
    db.add(member)
    db.flush()
    return member


def update(
    db: Session,
    member_id: UUID,
    data: ProjectMemberUpdate,
) -> ProjectMember:
    """Partially update a project membership.

    :class:`ProjectMember` has no mutable columns — ``id``,
    ``project_id``, ``user_id``, ``created_at`` and ``updated_at`` are
    all immutable. ``project_id`` / ``user_id`` form the natural key
    and must not be rewritten after the fact; ``updated_at`` is
    auto-stamped by the ORM on flush via ``onupdate=func.now()``.

    :class:`ProjectMemberUpdate` therefore exposes no fields; the
    service's empty allow-list formalises that contract defensively.
    This function exists for symmetry with the rest of the CRUD
    surface — it confirms the row exists (raising :class:`ValueError`
    if not) and returns the unmodified instance. Changing membership
    is a create/delete operation, not an in-place edit.

    Raises:
        ValueError: If the membership does not exist.
    """
    member = get_by_id(db, member_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — :class:`ProjectMemberUpdate` has no fields so
    # ``update_data`` is always empty. If a future schema change ever
    # adds a field without updating this allow-list, the field will
    # be silently dropped here rather than silently leaking through.
    allowed_fields: set[str] = set()

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(member, field, value)

    db.flush()
    return member


def delete(db: Session, member_id: UUID) -> None:
    """Hard-delete a project membership.

    ``project_members`` has no inbound FKs — no other table references
    it — so no dependency RESTRICT check is required. Outbound FKs
    (``project_id``, ``user_id``) both use ``ON DELETE CASCADE``, so
    deleting the parent project or user cleans up the membership
    automatically; this function is the explicit inverse, removing
    the membership row itself (the "remove from project" flow in the
    settings / team-management UI).

    Raises:
        ValueError: If the membership does not exist.
    """
    member = get_by_id(db, member_id)
    db.delete(member)
    db.flush()
