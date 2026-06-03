"""Tests for the Gate E backfill data migration (052, CR-NS-018 Phase 1).

Loads the migration's ``_run_backfill`` helper directly (it only uses the
passed bind, no alembic context) and exercises the dialogue→pipeline_message
transform against seeded data.
"""

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

from backend.db.models.dialogue import DialogueMessage, DialogueSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version

_MIG = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "052_gate_e_backfill.py"
_spec = importlib.util.spec_from_file_location("gate_e_backfill_migration", _MIG)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)
run_backfill = _mig._run_backfill


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
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, user, project


def _session(db_session, user, project, version_id):
    sess = DialogueSession(
        user_id=user.id,
        project_slug=project.slug,
        version_id=version_id,
    )
    db_session.add(sess)
    db_session.flush()
    return sess


def _msg(db_session, sess, *, author, status, content="x"):
    m = DialogueMessage(session_id=sess.id, author=author, content=content, status=status)
    db_session.add(m)
    db_session.flush()
    return m


def test_backfill_maps_and_skips(db_session):
    version, user, project = _make_version(db_session)
    linked = _session(db_session, user, project, version.id)
    _msg(db_session, linked, author="customer", status="pending", content="Q1")
    _msg(db_session, linked, author="designer", status="delivered", content="A1")
    _msg(db_session, linked, author="director", status="approved", content="D1")
    _msg(db_session, linked, author="customer", status="rejected", content="Q2")
    # Session with no version → its message must be skipped.
    orphan = _session(db_session, user, project, None)
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
    version, user, project = _make_version(db_session)
    linked = _session(db_session, user, project, version.id)
    _msg(db_session, linked, author="customer", status="delivered", content="only")

    first = run_backfill(db_session.connection())
    assert first == {"inserted": 1, "skipped": 0}

    second = run_backfill(db_session.connection())
    assert second == {"inserted": 0, "skipped": 0}

    total = db_session.execute(text("SELECT count(*) FROM pipeline_message WHERE stage = 'gate_e'")).scalar()
    assert total == 1
