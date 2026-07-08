"""Follow-up to Part 1 (per-app-changelog-part1-followup.md): a RELEASED version stays VERIFIED
after the §3.6 graduation note-move commit advances the app-repo HEAD past the anchored ``verified_sha``.

The regression: ``deploy._move_release_note_dir`` commits the moved ``RELEASE_NOTES.md`` into the app
repo AFTER the version was released + SHA-anchored. That commit advances HEAD past the anchored
``verified_sha``/``hotovo_sha``. ``version_verified`` compared the stored SHA to live HEAD with NO
``released`` short-circuit → the just-graduated ``v1.0.0`` read ``sha_drift`` (unverified) → it dropped
out of ``list_verified_versions`` → every later deploy of it was hard-blocked, and a released version
can't be re-verified (fatal for instance-per-customer: the 2nd customer deploy + any redeploy break).

Fix: ``version_verified`` short-circuits to verified for ``status == 'released'`` (a shipped release is
immutable + verified by definition — no post-release commit may un-verify it). Guardrail asserted here:
a NOT-released version whose HEAD moved past its anchor STILL reports drift (unchanged — that IS the real
safeguard catching code changing after a Verifikácia PASS).
"""

from __future__ import annotations

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

ANCHOR = "a" * 40  # the commit SHA the Verifikácia PASS was anchored to
MOVED_HEAD = "b" * 40  # HEAD AFTER the graduation note-move commit — past the anchor


def _seed_sha_anchored_version(db, *, version_number: str, status: str) -> Version:
    """A version carried to Hotovo (``current_stage='done'``) with a Verifikácia PASS SHA-anchored to
    ``ANCHOR`` — the exact state at the moment of the §3.6 graduation, before the note-move commit."""
    creator = User(
        username=f"vvr_{uuid.uuid4().hex[:8]}",
        email=f"vvr_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"VVR {uuid.uuid4().hex[:6]}",
        slug=f"vvr-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="version_verified released short-circuit fixture",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(
        project_id=project.id,
        version_number=version_number,
        name=version_number,
        status=status,
    )
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="auditor",
            status="done",
            next_action="",
        )
    )
    db.flush()
    # CR-V2-056: the latest Verifikácia PASS verdict, anchored to ANCHOR.
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia", "verified_sha": ANCHOR},
    )
    db.flush()
    return version


def test_released_version_stays_verified_after_head_moves_past_anchor(db_session):
    """After graduation (status='released', the note-move commit advanced HEAD past ``verified_sha``),
    ``version_verified`` returns verified — NOT drift. This is the exact gap the graduation test missed:
    it only checked the moved file/tree, never called ``version_verified`` afterward."""
    version = _seed_sha_anchored_version(db_session, version_number="v1.0.0", status="released")

    is_verified, provenance = orchestrator.version_verified(db_session, version.id, head=MOVED_HEAD)

    assert is_verified is True, "a released version must never un-verify on a post-release commit"
    assert provenance == "released"


def test_not_released_version_still_drifts_when_head_moves(db_session):
    """Guardrail — a NOT-released version whose HEAD moved past its anchored SHA STILL reports drift.
    Only ``released`` short-circuits; the drift detection that catches code changing after a Verifikácia
    PASS is byte-identical for every non-released status."""
    version = _seed_sha_anchored_version(db_session, version_number="v0.1.0", status="active")

    is_verified, provenance = orchestrator.version_verified(db_session, version.id, head=MOVED_HEAD)

    assert is_verified is False, "a not-released version whose HEAD drifted past its anchor must un-verify"
    assert provenance == "sha_drift"
