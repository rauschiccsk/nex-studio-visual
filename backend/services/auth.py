"""Authentication service — login logic, password verification, JWT issuance.

Orchestrates the login flow described in DESIGN.md Section 2.1:
    1. Look up user by username.
    2. Verify plaintext password against stored bcrypt hash.
    3. Find (or create) user session and bump ``token_version``.
    4. Issue a signed JWT with ``sub``, ``role``, ``exp`` claims.

All DB access is synchronous (pg8000).  Errors are signalled via
``ValueError`` so the router can translate them to the appropriate HTTP
status code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.foundation import User, UserSession
from backend.services import system_setting as system_setting_service


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def authenticate_user(db: Session, username: str, password: str) -> User:
    """Validate credentials and return the authenticated user.

    Raises:
        ValueError: If the username does not exist, the password is
            wrong, or the user is inactive.  The message is intentionally
            generic ("Invalid username or password" / "User account is
            inactive") to avoid leaking which field failed.
    """
    stmt = select(User).where(User.username == username)
    user = db.execute(stmt).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid username or password")

    if not user.is_active:
        raise ValueError("User account is inactive")

    return user


def _bump_token_version(db: Session, user_id: object) -> int:
    """Increment ``token_version`` on the user's session and return the new value.

    If no session exists yet, one is created with ``token_version = 1``.
    """
    stmt = select(UserSession).where(UserSession.user_id == user_id)
    session = db.execute(stmt).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if session is None:
        session = UserSession(user_id=user_id, token_version=1, last_seen_at=now)
        db.add(session)
        db.flush()
        return 1

    session.token_version = session.token_version + 1
    session.last_seen_at = now
    db.flush()
    return session.token_version


def create_access_token(user: User, token_version: int, expire_minutes: int) -> tuple[str, int]:
    """Create a signed JWT for the given user.

    Args:
        user: The authenticated user.
        token_version: Per-user version counter used to invalidate old
            tokens on logout / password change.
        expire_minutes: Token lifetime in minutes. Callers resolve this
            from :mod:`backend.services.system_setting` (key
            ``access_token_expire_minutes``) so it can be changed
            without code edits.

    Returns:
        Tuple of (encoded_jwt, expires_in_seconds).
    """
    expires_in = expire_minutes * 60
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expire_minutes)

    payload = {
        "sub": str(user.id),
        "role": user.role,
        "tv": token_version,
        # ``iat`` (issued-at) lets the frontend compute the renewal window as a
        # fraction of the *actual* token lifetime (exp - iat) instead of
        # hard-coding it — the sliding keep-alive renews at ~75% of lifetime.
        # Purely informational for the backend (never validated on decode).
        "iat": now,
        "exp": expire,
    }

    encoded = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return encoded, expires_in


def login(db: Session, username: str, password: str) -> tuple[User, str, int]:
    """Full login flow: authenticate → bump token version → issue JWT.

    Returns:
        Tuple of (user, access_token, expires_in_seconds).

    Raises:
        ValueError: On invalid credentials or inactive account.
    """
    user = authenticate_user(db, username, password)
    token_version = _bump_token_version(db, user.id)
    expire_minutes = system_setting_service.get_int(db, "access_token_expire_minutes")
    access_token, expires_in = create_access_token(user, token_version, expire_minutes)
    return user, access_token, expires_in


def refresh_session(db: Session, user: User) -> tuple[str, int]:
    """Issue a fresh access token for an already-authenticated session.

    Sliding expiration: unlike :func:`login`, this does NOT bump
    ``token_version`` — it re-issues a token for the SAME still-valid session
    (same user, same version) with a fresh ``access_token_expire_minutes``
    expiry. The caller must have already passed the ``get_current_user``
    dependency (token valid, non-expired, current ``token_version``), so an
    expired / bumped / invalid token is rejected upstream with 401 and never
    reaches here — a dead session can never be renewed.

    Returns:
        Tuple of (access_token, expires_in_seconds), same shape as
        :func:`login` minus the user (the router already holds it).

    Raises:
        ValueError: If no session row exists for the user (a token that
            outlived its session — treated as a dead session → 401 upstream).
    """
    token_version = get_token_version(db, user.id)
    if token_version is None:
        raise ValueError("Session not found for user")
    expire_minutes = system_setting_service.get_int(db, "access_token_expire_minutes")
    return create_access_token(user, token_version, expire_minutes)


def logout(db: Session, user_id: object) -> None:
    """Invalidate all tokens for a user by bumping ``token_version``.

    Any JWT whose ``tv`` claim is less than the new ``token_version``
    will be rejected by :func:`backend.core.security.get_current_user`.

    Raises:
        ValueError: If no session exists for the given user.
    """
    stmt = select(UserSession).where(UserSession.user_id == user_id)
    session = db.execute(stmt).scalar_one_or_none()

    if session is None:
        raise ValueError("Session not found for user")

    session.token_version = session.token_version + 1
    session.last_seen_at = datetime.now(timezone.utc)
    db.flush()


def get_token_version(db: Session, user_id: object) -> int | None:
    """Return the current ``token_version`` for a user, or ``None`` if no session exists."""
    stmt = select(UserSession.token_version).where(UserSession.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()
