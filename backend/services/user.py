"""Service layer for :class:`~backend.db.models.foundation.User`.

Provides the synchronous CRUD surface used by API routers. All methods accept
``db: Session`` as the first argument and only ever call ``session.flush()`` —
transaction commit is the router's responsibility. Errors are signalled via
``ValueError`` so the router can translate them to the appropriate HTTP
status code.

Design notes (per DESIGN.md §1.1 Foundation — Auth & Users and
:mod:`backend.db.models.foundation`):
    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer.
    * ``username`` and ``email`` are both ``UNIQUE`` — :func:`create` and
      :func:`update` validate these constraints before :meth:`Session.flush`
      so the router receives a clean :class:`ValueError` rather than a raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``role`` is constrained by a CHECK (``ri`` | ``ha`` | ``shu``). The
      Pydantic ``UserRole`` literal mirrors the DB constraint, so the
      service does not need to revalidate it — if an invalid value ever
      reaches the service (e.g. a bypassed schema) the DB CHECK will
      reject it on flush.
    * :func:`delete` proactively checks every inbound ``ondelete='RESTRICT'``
      FK (``projects.created_by``, ``bugs.created_by``,
      ``architect_sessions.created_by``, ``raw_specifications.created_by``,
      ``professional_specifications.approved_by``,
      ``design_documents.approved_by``) and raises :class:`ValueError` when
      references exist. ``user_sessions`` and ``project_members`` cascade
      on delete and therefore need no check.
    * ``is_active`` is the soft-disable flag — :func:`delete` is a hard
      delete reserved for test fixtures / admin tooling. Routine
      deactivation should :func:`update` with ``is_active=False``.
    * List filters (``role``, ``is_active``) support the settings page's
      user-management UI — "show all active `ri` users", etc.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.architect import ArchitectSession
from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import (
    DesignDocument,
    ProfessionalSpecification,
    RawSpecification,
)
from backend.schemas.user import UserCreate, UserRole, UserUpdate


def list_users(
    db: Session,
    *,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[User]:
    """Return users filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently added
    users appear first, matching the settings-page user list convention.

    Args:
        db: Active SQLAlchemy session.
        role: Optional role filter (``ri`` | ``ha`` | ``shu``).
        is_active: Optional active-flag filter. ``None`` (the default)
            returns both active and soft-disabled users.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`User` instances.
    """
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, user_id: UUID) -> User:
    """Return a single user by primary key.

    Raises:
        ValueError: If no user with the supplied ``user_id`` exists. The
            router converts this to an HTTP 404 response.
    """
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    return user


def _get_by_username(db: Session, username: str) -> Optional[User]:
    """Internal helper — look up a user by unique username."""
    stmt = select(User).where(User.username == username)
    return db.execute(stmt).scalar_one_or_none()


def _get_by_email(db: Session, email: str) -> Optional[User]:
    """Internal helper — look up a user by unique email."""
    stmt = select(User).where(User.email == email)
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: UserCreate) -> User:
    """Create a new user.

    Validates both unique constraints (``username``, ``email``) before
    insertion so the caller receives a clean :class:`ValueError` (HTTP 409
    at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`User` with its server-
        generated ``id``, ``created_at`` and ``updated_at`` populated.

    Raises:
        ValueError: If another user already uses the same ``username`` or
            ``email``.
    """
    if _get_by_username(db, data.username) is not None:
        raise ValueError(f"User with username {data.username!r} already exists")
    if _get_by_email(db, data.email) is not None:
        raise ValueError(f"User with email {data.email!r} already exists")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=data.password_hash,
        role=data.role,
        is_active=data.is_active,
    )
    db.add(user)
    db.flush()
    return user


def update(db: Session, user_id: UUID, data: UserUpdate) -> User:
    """Partially update a user.

    Only ``username``, ``email``, ``password_hash``, ``role`` and
    ``is_active`` may be changed — ``id``, ``created_at`` and
    ``updated_at`` are immutable (``updated_at`` is refreshed automatically
    by the ORM ``onupdate=func.now()`` trigger). Fields that are ``None``
    in the payload are treated as "leave unchanged" to support PATCH
    semantics.

    Uniqueness of ``username`` and ``email`` is re-validated when those
    fields are changed so the caller receives a clean :class:`ValueError`
    rather than a DB-level integrity error.

    Raises:
        ValueError: If the user does not exist, or if a new ``username``
            / ``email`` collides with another user.
    """
    user = get_by_id(db, user_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields, but
    # silently dropping any that slip through keeps the service honest.
    allowed_fields = {"username", "email", "password_hash", "role", "is_active"}

    # Uniqueness checks only for actually-changing values.
    new_username = update_data.get("username")
    if new_username is not None and new_username != user.username:
        existing = _get_by_username(db, new_username)
        if existing is not None and existing.id != user.id:
            raise ValueError(f"User with username {new_username!r} already exists")

    new_email = update_data.get("email")
    if new_email is not None and new_email != user.email:
        existing = _get_by_email(db, new_email)
        if existing is not None and existing.id != user.id:
            raise ValueError(f"User with email {new_email!r} already exists")

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(user, field, value)

    db.flush()
    return user


def _has_restrict_dependencies(db: Session, user_id: UUID) -> Optional[str]:
    """Return a human-readable reason if any RESTRICT FK references the user.

    Checks every inbound ``ondelete='RESTRICT'`` FK on ``users.id``:
      * ``projects.created_by``
      * ``bugs.created_by``
      * ``architect_sessions.created_by``
      * ``raw_specifications.created_by``
      * ``professional_specifications.approved_by``
      * ``design_documents.approved_by``

    ``user_sessions.user_id`` and ``project_members.user_id`` use
    ``ON DELETE CASCADE`` and therefore impose no constraint.

    Returns ``None`` when the user is safe to delete.
    """
    checks: list[tuple[type, object, str]] = [
        (Project, Project.created_by, "projects"),
        (Bug, Bug.created_by, "bugs"),
        (ArchitectSession, ArchitectSession.created_by, "architect_sessions"),
        (RawSpecification, RawSpecification.created_by, "raw_specifications"),
        (
            ProfessionalSpecification,
            ProfessionalSpecification.approved_by,
            "professional_specifications",
        ),
        (DesignDocument, DesignDocument.approved_by, "design_documents"),
    ]
    for model, column, table in checks:
        exists = db.execute(select(model.id).where(column == user_id).limit(1)).first()
        if exists is not None:
            return table
    return None


def delete(db: Session, user_id: UUID) -> None:
    """Hard-delete a user.

    Before deletion, every inbound ``ondelete='RESTRICT'`` foreign key is
    checked. When any dependent row exists, :class:`ValueError` is raised
    instead of letting the DB reject the delete with an
    :class:`~sqlalchemy.exc.IntegrityError`. Routine deactivation should
    prefer :func:`update` with ``is_active=False`` (soft disable).

    Raises:
        ValueError: If the user does not exist, or if any project, bug,
            architect session, raw specification, professional
            specification, or design document still references them.
    """
    user = get_by_id(db, user_id)

    blocking = _has_restrict_dependencies(db, user.id)
    if blocking is not None:
        raise ValueError(f"Cannot delete User {user.id}: referenced by existing {blocking}")

    db.delete(user)
    db.flush()
