"""Authentication / authorization helpers used by API routers.

This module provides the JWT-bearer dependencies that gate the REST
endpoints. The full ``/auth/login`` flow (DESIGN.md §2.1) is delivered in
a later feat — this module ships the *server-side* surface that the
versions router (and every subsequent gated router) needs:

* :func:`get_current_user` — resolve and return the
  :class:`~backend.db.models.foundation.User` row identified by the
  ``Authorization: Bearer <jwt>`` header. Raises HTTP 401 on missing,
  malformed, expired or otherwise invalid tokens, or when the resolved
  user is missing / inactive.
* :func:`require_ri_role` — wraps :func:`get_current_user` and rejects
  any non-``ri`` user with HTTP 403. Mirrors DESIGN.md §2.6 ``POST
  /projects/{id}/versions`` and ``POST /versions/{id}/release`` ("Auth:
  ``ri`` role only.").

Tokens are HS256-signed with :data:`backend.config.settings.secret_key`
and carry the user id in the standard ``sub`` claim. Tests override these
dependencies via FastAPI's ``app.dependency_overrides`` mechanism so unit
tests do not need real JWTs.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.services import auth as auth_service

# ``auto_error=False`` so a missing header surfaces as ``credentials is
# None`` and we can raise our own 401 with the WWW-Authenticate header
# wired in (FastAPI's default 403 is wrong for missing credentials).
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return the :class:`User` identified by the bearer JWT.

    The JWT is signed with :data:`settings.secret_key` (HS256) and
    carries the user id as the ``sub`` claim. Tokens are short-lived —
    expiry is enforced by ``python-jose`` automatically.

    Raises:
        HTTPException 401: If the ``Authorization`` header is missing,
            uses a non-``Bearer`` scheme, the token is malformed /
            expired, or the resolved user is missing or inactive.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key,
            algorithms=["HS256"],
        )
        user_id = UUID(str(payload["sub"]))
    except (JWTError, KeyError, ValueError) as exc:
        raise unauthorized from exc

    # Validate token_version claim against DB — logout bumps tv to
    # invalidate all previously-issued JWTs.
    tv_claim = payload.get("tv")
    if tv_claim is not None:
        db_tv = auth_service.get_token_version(db, user_id)
        if db_tv is not None and tv_claim < db_tv:
            raise unauthorized

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_ri_role(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow the request only when the authenticated user has role ``ri``.

    DESIGN.md §2.6 reserves version create / update / release to ``ri``
    users. Other authenticated users (``ha`` / ``shu``) receive HTTP 403.
    """
    if current_user.role != "ri":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation requires the 'ri' role",
        )
    return current_user


def require_ha_or_above(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow ``ri`` and ``ha`` users; reject ``shu`` with HTTP 403.

    Mirrors NEX Command's ``require_ha_or_above`` (used for write-level
    operations: create/update KB documents, run audits, manage projects).
    The Shuhari hierarchy is ``ri > ha > shu``; this gate covers the
    upper two roles.
    """
    if current_user.role not in ("ri", "ha"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation requires the 'ri' or 'ha' role",
        )
    return current_user


def require_shu_or_above(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow any authenticated user (``ri``/``ha``/``shu``).

    Equivalent to :func:`get_current_user` but expressed explicitly so
    routes that document a Shuhari floor can still reference a named
    dependency. Useful for audit clarity ("this endpoint requires at
    least shu") and for symmetry with :func:`require_ha_or_above` and
    :func:`require_ri_role`.
    """
    # All roles in our model satisfy this; we only need a valid user.
    return current_user


def has_full_kb_access(user: User) -> bool:
    """True if the user can read every KB document, including any restricted category.

    Mirrors NEX Command's ``_has_full_access(user)``. In NEX Studio's
    flatter role model ``ri`` is the equivalent of NEX Command's
    ``director`` role; ``ha`` and ``shu`` users are filtered by
    category and per-project access (see :mod:`backend.utils.kb_access`).
    """
    return user.role == "ri"
