"""Audit P0 (2026-07-12): Kontrola never signs off on a RED build.

``kontrola_done`` is pass-BLIND — it flips True as soon as the AI partner records its honest ``gate_report``,
EVEN when the deterministic runtime floor is red (the app did not boot / acceptance did not pass). So a
non-booting conversation build could be marked Hotovo → verified → deployable to a real customer in one click.
``kontrola_floor_red`` + ``kontrola_passed`` close that: a red floor keeps ``kontrola_passed`` False, so the
board drops the "Označiť ako hotové" button and ``apply_action('hotovo')`` refuses.

Runs against the real v2 test DB (SAVEPOINT-isolated via the ``db_session`` fixture).
"""

from __future__ import annotations

import uuid as _uuid

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.orchestrator import _record_message


def _seed_version(db) -> Version:
    suffix = _uuid.uuid4().hex[:8]
    user = User(
        username=f"kp_{suffix}",
        email=f"kp_{suffix}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(user)
    db.flush()
    project = Project(
        name=f"Kontrola Passed Proj {suffix}",
        slug=f"kontrola-passed-{suffix}",
        type="standard",
        auth_mode="password",
        description="Audit P0 kontrola_passed test project.",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.1.0", status="active")
    db.add(version)
    db.flush()
    return version


def _prog_complete(db, vid):
    return _record_message(
        db,
        version_id=vid,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Programovanie dokončené",
        payload={"programming_complete": True},
    )


def _kontrola_report(db, vid):
    """The AI partner's honest self-check gate_report — recorded on EVERY kontrola turn, red or green."""
    return _record_message(
        db,
        version_id=vid,
        stage="priprava",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="Kontrola",
        payload={"kontrola": True},
    )


def _floor_red_note(db, vid):
    """The system's red-floor note — recorded (AFTER the gate_report) only when the runtime floor is red."""
    return _record_message(
        db,
        version_id=vid,
        stage="priprava",
        author="system",
        recipient="manazer",
        kind="notification",
        content="beh appky je ČERVENÝ",
        payload={"kontrola_floor_red": True, "kontrola": True},
    )


def test_kontrola_passed_true_when_green(db_session):
    v = _seed_version(db_session)
    _prog_complete(db_session, v.id)
    _kontrola_report(db_session, v.id)
    assert orchestrator.kontrola_done(db_session, v.id) is True
    assert orchestrator.kontrola_floor_red(db_session, v.id) is False
    assert orchestrator.kontrola_passed(db_session, v.id) is True


def test_kontrola_passed_false_when_floor_red(db_session):
    v = _seed_version(db_session)
    _prog_complete(db_session, v.id)
    _kontrola_report(db_session, v.id)
    _floor_red_note(db_session, v.id)  # red note past the report → the latest kontrola is red
    # kontrola RAN (done=True) but the floor is RED, so it did NOT pass — Hotovo must stay off.
    assert orchestrator.kontrola_done(db_session, v.id) is True
    assert orchestrator.kontrola_floor_red(db_session, v.id) is True
    assert orchestrator.kontrola_passed(db_session, v.id) is False


def test_green_kontrola_after_red_clears_the_floor(db_session):
    v = _seed_version(db_session)
    _prog_complete(db_session, v.id)
    _kontrola_report(db_session, v.id)
    _floor_red_note(db_session, v.id)  # turn 1: red
    _kontrola_report(db_session, v.id)  # turn 2: green — newer gate_report, no floor note
    # The newest kontrola gate_report outranks the old floor note → floor clear, passed again.
    assert orchestrator.kontrola_floor_red(db_session, v.id) is False
    assert orchestrator.kontrola_passed(db_session, v.id) is True
