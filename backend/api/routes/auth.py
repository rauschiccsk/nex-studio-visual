"""Authentication router — login, logout, session info.

Implements DESIGN.md Section 2.1 auth endpoints under ``/api/v1/auth``.
All endpoints are synchronous ``def`` (pg8000 driver, sync DB via
threadpool).  Business logic is delegated to
:mod:`backend.services.auth`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.auth import AuthUser, LoginRequest, LoginResponse
from backend.services import auth as auth_service

router = APIRouter(tags=["Auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Authenticate a user and return a JWT access token.

    Validates username + bcrypt password, bumps the session
    ``token_version`` and issues a signed JWT with ``sub``, ``role``
    and ``exp`` claims.
    """
    try:
        user, access_token, expires_in = auth_service.login(db, body.username, body.password)
        db.commit()
    except ValueError as exc:
        msg = str(exc)
        if "inactive" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=AuthUser.model_validate(user),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Log out the current user by invalidating all issued tokens.

    Bumps ``token_version`` on the user's session so that any JWT
    with a stale ``tv`` claim will be rejected on subsequent requests.
    """
    try:
        auth_service.logout(db, current_user.id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=AuthUser,
    status_code=status.HTTP_200_OK,
)
def me(
    current_user: User = Depends(get_current_user),
) -> AuthUser:
    """Return the currently authenticated user's profile."""
    return AuthUser.model_validate(current_user)
