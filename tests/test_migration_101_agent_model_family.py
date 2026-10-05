"""Migration 101 turns a stored model VERSION into its family (ICCINT-167).

Runs the real ``upgrade()`` / ``downgrade()`` through Alembic ``Operations`` on the test transaction — not
a copy of its SQL — so the test breaks the moment the migration stops doing what it claims.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.db.models.foundation import User, UserAgentSettings

_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "101_agent_model_family.py"


def _load():
    spec = importlib.util.spec_from_file_location("m101", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(db_session: Any, fn_name: str) -> None:
    ctx = MigrationContext.configure(db_session.connection())
    with Operations.context(ctx):
        getattr(_load(), fn_name)()


def _user(db_session: Any) -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(u)
    db_session.flush()
    return u


def _row(db_session: Any, user_id: uuid.UUID, role: str) -> tuple:
    return db_session.execute(
        text("SELECT model, helper_model FROM user_agent_settings WHERE user_id = :u AND agent_role = :r"),
        {"u": user_id, "r": role},
    ).one()


def test_upgrade_maps_versions_to_families_and_drops_unknown(db_session):
    u = _user(db_session)
    db_session.add_all(
        [
            UserAgentSettings(
                user_id=u.id, agent_role="ai_agent", model="claude-opus-4-8", helper_model="claude-haiku-4-5-20251001"
            ),
            UserAgentSettings(user_id=u.id, agent_role="auditor", model="claude-sonnet-4-6", helper_model="gpt-4"),
        ]
    )
    db_session.flush()

    _run(db_session, "upgrade")

    assert _row(db_session, u.id, "ai_agent") == ("opus", "haiku")
    # A name of no known family would be unreadable through the API's family Literal → back to the default.
    assert _row(db_session, u.id, "auditor") == ("sonnet", None)


def test_upgrade_keeps_a_family_and_null_as_they_are(db_session):
    u = _user(db_session)
    db_session.add(UserAgentSettings(user_id=u.id, agent_role="ai_agent", model="opus", helper_model=None))
    db_session.flush()
    _run(db_session, "upgrade")
    assert _row(db_session, u.id, "ai_agent") == ("opus", None)


def test_downgrade_returns_families_to_the_default(db_session):
    u = _user(db_session)
    db_session.add(UserAgentSettings(user_id=u.id, agent_role="ai_agent", model="opus", helper_model="haiku"))
    db_session.flush()
    _run(db_session, "downgrade")
    assert _row(db_session, u.id, "ai_agent") == (None, None)
