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
      FK (``projects.created_by``, ``bugs.created_by``) and raises
      :class:`ValueError` when references exist. ``user_sessions`` cascades
      on delete and therefore needs no check.
    * ``is_active`` is the soft-disable flag — :func:`delete` is a hard
      delete reserved for test fixtures / admin tooling. Routine
      deactivation should :func:`update` with ``is_active=False``.
    * List filters (``role``, ``is_active``) support the settings page's
      user-management UI — "show all active `ri` users", etc.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import Project
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


def count_users(
    db: Session,
    *,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
) -> int:
    """Return the total number of users matching the given filters.

    Mirrors the ``role`` / ``is_active`` filters of :func:`list_users` so a
    paginated response can report the unfiltered total alongside the
    current page of items.

    Args:
        db: Active SQLAlchemy session.
        role: Optional role filter (``ri`` | ``ha`` | ``shu``).
        is_active: Optional active-flag filter.

    Returns:
        Total number of rows matching the filters.
    """
    stmt = select(func.count()).select_from(User)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    return int(db.execute(stmt).scalar_one())


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
    """Create a new user with bcrypt-hashed password and initial session.

    Validates both unique constraints (``username``, ``email``) before
    insertion so the caller receives a clean :class:`ValueError` (HTTP 409
    at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.

    The plaintext ``data.password`` is hashed with bcrypt before being
    stored in the ``password_hash`` column.  A :class:`UserSession` row
    is created alongside the user with ``token_version=0`` so that
    subsequent JWT issuance has a session to reference.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload (contains plaintext password).

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

    hashed = bcrypt.hashpw(
        data.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hashed,
        role=data.role,
        is_active=data.is_active,
        first_name=data.first_name,
        last_name=data.last_name,
    )
    db.add(user)
    db.flush()

    # Create initial user session with token_version=0
    session = UserSession(user_id=user.id, token_version=0)
    db.add(session)
    db.flush()

    return user


def update(db: Session, user_id: UUID, data: UserUpdate) -> User:
    """Partially update a user.

    Only ``username``, ``email``, ``role`` and
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
    allowed_fields = {
        "username",
        "email",
        "role",
        "is_active",
        "first_name",
        "last_name",
    }

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

    ``user_sessions.user_id`` uses ``ON DELETE CASCADE`` and therefore
    imposes no constraint.

    Returns ``None`` when the user is safe to delete.
    """
    checks: list[tuple[type, object, str]] = [
        (Project, Project.created_by, "projects"),
        (Bug, Bug.created_by, "bugs"),
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
        ValueError: If the user does not exist, or if any project or bug
            still references them.
    """
    user = get_by_id(db, user_id)

    blocking = _has_restrict_dependencies(db, user.id)
    if blocking is not None:
        raise ValueError(f"Cannot delete User {user.id}: referenced by existing {blocking}")

    db.delete(user)
    db.flush()


def change_password(
    db: Session,
    user_id: UUID,
    new_password: str,
    current_user: User,
) -> User:
    """Change a user's password, hash it with bcrypt, and rotate tokens.

    Authorization rules:
        * A user with role ``ri`` (Director/Senior) may change **any** user's
          password — this is the admin "reset password" flow.
        * Users with role ``ha`` or ``shu`` may only change **their own**
          password.

    After updating ``password_hash`` the function bumps the user's
    ``token_version`` via :func:`backend.services.auth._bump_token_version`
    so that all previously issued JWTs for that user are invalidated.

    Args:
        db: Active SQLAlchemy session.
        user_id: The target user whose password will be changed.
        new_password: The new plaintext password (will be bcrypt-hashed).
        current_user: The authenticated user performing the action.

    Returns:
        The updated :class:`User` instance with the new ``password_hash``.

    Raises:
        ValueError: If ``current_user`` lacks permission (not ``ri`` and
            ``user_id != current_user.id``), or if the target user does
            not exist.
    """
    # --- Authorization ---
    if current_user.role != "ri" and current_user.id != user_id:
        raise ValueError("Insufficient permissions: only ri role can change another user's password")

    # --- Fetch target user ---
    user = get_by_id(db, user_id)

    # --- Hash and persist ---
    hashed = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")
    user.password_hash = hashed
    db.flush()

    # --- Rotate tokens (invalidate all existing JWTs) ---
    from backend.services.auth import _bump_token_version

    _bump_token_version(db, user_id)

    return user
