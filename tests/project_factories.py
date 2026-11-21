"""A person, a project and a version for tests — the rows most tests of a project's work start from.

Kept apart from any one feature's tests (until DEV-455 they lived in the Zásobník's tests, which are gone).
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version


def _make_user(db_session: Any, role: str = "ha") -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session: Any, owner: User) -> Project:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session: Any, project: Project) -> Version:
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().int % 10**4}.0")
    db_session.add(version)
    db_session.flush()
    return version
