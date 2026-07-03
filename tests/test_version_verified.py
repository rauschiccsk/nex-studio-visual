"""CR-V2-056 layer-1 reality-anchoring: version_verified() computes "verified" from the live repo (the PASS
verdict's bound commit SHA vs the current HEAD), not a stored 'done' snapshot — so a moved HEAD auto-un-verifies
(kills the frozen-PASS bug). Total function, graceful degradation. Exercised against the real v2 branch DB."""

import uuid

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

# (pytest asyncio_mode=auto — sync tests run fine.)


def _mk_version(db):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@e.com",
        password_hash="x",
        role="ri",
    )
    db.add(user)
    db.flush()
    project = Project(
        name="P",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    v = Version(project_id=project.id, version_number="v0.1.0", name="dev")
    db.add(v)
    db.flush()
    return v


def _pass(db, version_id, **extra):
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia", **extra},
    )
    db.flush()


def test_no_pass_is_not_verified(db_session):
    v = _mk_version(db_session)
    assert orchestrator.version_verified(db_session, v.id, head="abc") == (False, "no_pass")


def test_pass_without_sha_grandfathers_unbound(db_session):
    # A PASS recorded before the SHA anchor shipped (or repo unreadable at PASS) → grandfathered, NOT un-verified.
    v = _mk_version(db_session)
    _pass(db_session, v.id)
    assert orchestrator.version_verified(db_session, v.id, head="abc") == (True, "unbound")


def test_legacy_grandfathered(db_session):
    v = _mk_version(db_session)
    _pass(db_session, v.id, verified_sha="legacy")
    assert orchestrator.version_verified(db_session, v.id, head="abc") == (True, "legacy")


def test_sha_match_is_verified(db_session):
    v = _mk_version(db_session)
    _pass(db_session, v.id, verified_sha="deadbeef")
    assert orchestrator.version_verified(db_session, v.id, head="deadbeef") == (True, "sha_match")


def test_sha_drift_un_verifies(db_session):
    # THE frozen-PASS fix: PASS bound to commit X; HEAD moved to Y (work landed outside the pipeline) → not verified.
    v = _mk_version(db_session)
    _pass(db_session, v.id, verified_sha="83769e0")
    assert orchestrator.version_verified(db_session, v.id, head="96cce6b") == (False, "sha_drift")


def test_repo_unreadable_does_not_un_verify(db_session):
    # Our OWN read failure (no repo on disk for a library/no-checkout project) never un-verifies a version.
    v = _mk_version(db_session)
    _pass(db_session, v.id, verified_sha="deadbeef")
    # head=None → version_verified reads _repo_head(PROJECTS_ROOT/slug); the test slug has no repo dir → None.
    assert orchestrator.version_verified(db_session, v.id, head=None) == (True, "repo_unreadable")


def test_fix_directive_after_pass_makes_it_stale(db_session):
    # CR-V2-055 interaction: a Manažér fix directive (return) newer than the PASS → re-judge pending → not verified.
    v = _mk_version(db_session)
    _pass(db_session, v.id, verified_sha="deadbeef")
    orchestrator._record_message(
        db_session,
        version_id=v.id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content="fix X",
        payload={"phase": "verifikacia"},
    )
    db_session.flush()
    assert orchestrator.version_verified(db_session, v.id, head="deadbeef") == (False, "no_pass")
