"""Tests for the Gate E backfill data migration (052, CR-NS-018 Phase 1).

Loads the migration's ``_run_backfill`` helper directly (it only uses the
passed bind, no alembic context) and exercises the dialogue→pipeline_message
transform against seeded data.

The ``dialogue_*`` ORM models were decommissioned in v0.7.1 P1 (migration 068
drops the tables), so this test no longer seeds via the ORM. It recreates the
two source tables with the minimal shape the backfill SQL reads — ``id`` /
``version_id`` on ``dialogue_sessions`` and ``session_id`` / ``author`` /
``content`` / ``status`` / ``created_at`` on ``dialogue_messages`` — via raw
DDL inside the SAVEPOINT-isolated transaction (rolled back after the test), then
seeds rows with raw SQL. This keeps the backfill→``pipeline_message`` validation
(maps + skips + idempotent) intact while staying independent of the removed
models. ``052`` itself uses raw ``sa.text`` SQL, so the helper runs unchanged.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version

# v2.0.0-dev: this exercises the Gate E backfill (migration 052) which writes v1 ``gate_e`` /
# ``customer`` / ``designer`` pipeline_message rows the v2 CHECKs reject. Gate E + its dialogue layer are
# v1 engine behaviour (the dialogue tables were already dropped in 068); the v2 upfront-completeness pass
# is rebuilt in Milestone C/D. Kept as the SPEC of that backfill; deferred meanwhile.
pytestmark = pytest.mark.skip(reason="v1 engine behaviour — replaced by v2 in Milestone C/D")

_MIG = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "052_gate_e_backfill.py"
_spec = importlib.util.spec_from_file_location("gate_e_backfill_migration", _MIG)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)
run_backfill = _mig._run_backfill


def _create_dialogue_tables(db_session) -> None:
    """Recreate the minimal ``dialogue_*`` source schema the backfill reads.

    The ORM models are gone (migration 068), so ``create_all`` no longer
    provisions these tables. The DDL runs inside the test's SAVEPOINT
    transaction and is dropped by the outer rollback at teardown.
    """
    db_session.execute(
        text(
            "CREATE TABLE dialogue_sessions ("
            "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            "  version_id UUID NULL,"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
    )
    db_session.execute(
        text(
            "CREATE TABLE dialogue_messages ("
            "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            "  session_id UUID NOT NULL,"
            "  author VARCHAR(20) NOT NULL,"
            "  content TEXT NOT NULL,"
            "  status VARCHAR(20) NOT NULL,"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
    )


def _make_version(db_session) -> Version:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, user, project


def _session(db_session, version_id) -> uuid.UUID:
    sid = uuid.uuid4()
    db_session.execute(
        text("INSERT INTO dialogue_sessions (id, version_id) VALUES (:sid, :vid)"),
        {"sid": str(sid), "vid": str(version_id) if version_id is not None else None},
    )
    return sid


def _msg(db_session, session_id, *, author, status, content="x") -> None:
    db_session.execute(
        text(
            "INSERT INTO dialogue_messages (session_id, author, content, status) "
            "VALUES (:sid, :author, :content, :status)"
        ),
        {"sid": str(session_id), "author": author, "content": content, "status": status},
    )


def test_backfill_maps_and_skips(db_session):
    _create_dialogue_tables(db_session)
    version, _user, _project = _make_version(db_session)
    linked = _session(db_session, version.id)
    _msg(db_session, linked, author="customer", status="pending", content="Q1")
    _msg(db_session, linked, author="designer", status="delivered", content="A1")
    _msg(db_session, linked, author="director", status="approved", content="D1")
    _msg(db_session, linked, author="customer", status="rejected", content="Q2")
    # Session with no version → its message must be skipped.
    orphan = _session(db_session, None)
    _msg(db_session, orphan, author="customer", status="delivered", content="orphan")

    counts = run_backfill(db_session.connection())
    assert counts == {"inserted": 4, "skipped": 1}

    rows = db_session.execute(
        text(
            "SELECT author, recipient, kind, content, status, stage "
            "FROM pipeline_message WHERE version_id = :vid ORDER BY content"
        ),
        {"vid": str(version.id)},
    ).all()
    by_content = {r[3]: r for r in rows}
    assert len(rows) == 4
    assert all(r[5] == "gate_e" for r in rows)

    # Q1: customer/pending → designer / question / pending
    assert by_content["Q1"][:5] == ("customer", "designer", "question", "Q1", "pending")
    # A1: designer/delivered → customer / answer / delivered
    assert by_content["A1"][:5] == ("designer", "customer", "answer", "A1", "delivered")
    # D1: director/approved → designer / directive / delivered
    assert by_content["D1"][:5] == ("director", "designer", "directive", "D1", "delivered")
    # Q2: customer/rejected → designer / question / archived
    assert by_content["Q2"][:5] == ("customer", "designer", "question", "Q2", "archived")
    # orphan content must not appear
    assert "orphan" not in by_content


def test_backfill_idempotent(db_session):
    _create_dialogue_tables(db_session)
    version, _user, _project = _make_version(db_session)
    linked = _session(db_session, version.id)
    _msg(db_session, linked, author="customer", status="delivered", content="only")

    first = run_backfill(db_session.connection())
    assert first == {"inserted": 1, "skipped": 0}

    second = run_backfill(db_session.connection())
    assert second == {"inserted": 0, "skipped": 0}

    total = db_session.execute(text("SELECT count(*) FROM pipeline_message WHERE stage = 'gate_e'")).scalar()
    assert total == 1
