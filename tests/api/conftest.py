"""Shared test helpers for auth API tests."""

from __future__ import annotations

import bcrypt

from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import ModuleDependency, Project, ProjectModule  # noqa: F401


def hash_password(plain: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")


def seed_user(
    db_session,
    *,
    username="admin",
    password="Nex123",
    role="ri",
    is_active=True,
):
    """Insert a user + session into the test DB and return the user."""
    user = User(
        username=username,
        email=f"{username}@test.local",
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(user_id=user.id, token_version=0)
    db_session.add(session)
    db_session.flush()

    return user


def login_user(client, username="admin", password="Nex123") -> str:
    """Login and return the access token."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]
