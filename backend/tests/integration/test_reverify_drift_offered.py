"""The drift re-verify offering (CR-V2-057, self-sufficiency batch 2026-07-10).

``overit_znovu`` ("Over znova") is a fully-implemented ``apply_action`` handler that re-runs the independent
Auditor against current HEAD when a version's verified green has DRIFTED (the PASS-bound commit SHA no longer
matches HEAD). Until now it was NEVER offered anywhere — ``determine_available_actions`` is state-only and
can't do the repo HEAD read, so the board route (which DOES compute ``verified_provenance``) is the only place
that can surface it. These pin the route finalizer (``_board``) that appends ``overit_znovu`` to
``available_actions`` EXACTLY when the live provenance is ``sha_drift`` AND the pipeline state is settled
(``done`` / ``awaiting_manazer``) — the same precondition the handler fail-closes on.

``version_verified`` is monkeypatched here (it is exercised end-to-end in test_version_verified_released.py);
these tests target the route's OFFERING logic, not the drift computation.

Runs against the real v2 DB (test DB :9178, SAVEPOINT-isolated via the ``db_session`` fixture).
"""

from __future__ import annotations

import uuid as _uuid

from backend.api.routes.pipeline import _board
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import deploy as deploy_service
from backend.services import orchestrator


def _seed_settled_version(db, *, state_status: str) -> Version:
    """A version with a PipelineState at the given status (``done`` / ``awaiting_manazer`` = settled)."""
    creator = User(
        username=f"rv_{_uuid.uuid4().hex[:8]}",
        email=f"rv_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"Reverify {_uuid.uuid4().hex[:6]}",
        slug=f"reverify-{_uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="drift re-verify offering test",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v1.0.0", name="v1.0.0", status="active")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="verifikacia" if state_status == "awaiting_manazer" else "done",
            current_actor="auditor",
            status=state_status,
            next_action="",
        )
    )
    db.flush()
    return version


def test_overit_znovu_offered_when_drifted_and_done(db_session, monkeypatch) -> None:
    """A settled (Hotovo/``done``) version whose verified green drifted past HEAD → the board offers
    ``overit_znovu`` (its ONLY action — the done state otherwise has an empty set)."""
    version = _seed_settled_version(db_session, state_status="done")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))

    board = _board(db_session, version.id)

    assert "overit_znovu" in board.available_actions


def test_overit_znovu_offered_when_drifted_and_awaiting_manazer(db_session, monkeypatch) -> None:
    """A passed-but-not-yet-signed version (``awaiting_manazer`` at Verifikácia) that drifted also offers
    ``overit_znovu`` — alongside the normal settle actions."""
    version = _seed_settled_version(db_session, state_status="awaiting_manazer")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))

    board = _board(db_session, version.id)

    assert "overit_znovu" in board.available_actions


def test_overit_znovu_offered_when_hotovo_drifted(db_session, monkeypatch) -> None:
    """audit #8: a CONVERSATION build's manager Hotovo signature that drifted past HEAD (``hotovo_drift``) also
    offers ``overit_znovu`` — previously ONLY ``sha_drift`` did, leaving a drifted conversation build a DEAD END
    (it could neither deploy — not verified — nor re-verify — no button)."""
    version = _seed_settled_version(db_session, state_status="done")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "hotovo_drift"))

    board = _board(db_session, version.id)

    assert "overit_znovu" in board.available_actions


def test_overit_znovu_not_offered_when_hotovo_matches_head(db_session, monkeypatch) -> None:
    """Guardrail — a conversation build whose Hotovo signature MATCHES HEAD (``hotovo_match``) has nothing to
    re-verify, so the button must not appear."""
    version = _seed_settled_version(db_session, state_status="done")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (True, "hotovo_match"))

    board = _board(db_session, version.id)

    assert "overit_znovu" not in board.available_actions


def test_overit_znovu_not_offered_when_verified_matches_head(db_session, monkeypatch) -> None:
    """Guardrail — a freshly-verified version whose SHA MATCHES HEAD (no drift) must NOT offer a re-verify
    (there is nothing to re-check); only ``sha_drift`` surfaces the button."""
    version = _seed_settled_version(db_session, state_status="awaiting_manazer")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (True, "sha_match"))

    board = _board(db_session, version.id)

    assert "overit_znovu" not in board.available_actions


def test_overit_znovu_not_offered_mid_build_even_if_drifted(db_session, monkeypatch) -> None:
    """Guardrail — drift on an UNSETTLED state (a new build running on top of a previously-passed version)
    must NOT offer a re-verify: re-verifying mid-build is meaningless. The handler fail-closes on the same
    settled-state precondition, so the offer must match it."""
    version = _seed_settled_version(db_session, state_status="awaiting_manazer")
    # Force an in-flight build state (not settled) despite a drifted provenance.
    state = db_session.query(PipelineState).filter_by(version_id=version.id).one()
    state.status = "agent_working"
    state.current_stage = "programovanie"
    db_session.flush()
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))

    board = _board(db_session, version.id)

    assert "overit_znovu" not in board.available_actions


# ---------------------------------------------------------------------------
# v4.0.54 — the SAME drift, seen from the UAT/PROD screen (``deploy.deployability``)
#
# Pressing "Over znova" takes the version OUT of the finished state for the WHOLE run (``apply_action``'s
# ``overit_znovu``: current_stage → 'verifikacia' for sha_drift / 'priprava' for hotovo_drift, status →
# 'agent_working'), so ``list_verified_versions`` stays EMPTY the entire time and the deploy screen would look
# byte-identical before and after the click — for minutes on end.
#
# ``deployability`` therefore needs EVIDENCE that the running turn IS the re-verification: a busy version is
# not automatically re-verifying (an ordinary chat turn or a build round also runs ``agent_working``), and
# only the re-verification ends by re-anchoring itself. These pin all three drifted shapes apart.
# ---------------------------------------------------------------------------


def test_deployability_reverify_running_when_drifted_reverify_in_flight(db_session, monkeypatch) -> None:
    """A drifted version whose RE-VERIFICATION is in flight reports ``reverify_running`` — and offers no
    button, because the run the button would start is already going.

    The evidence is the shape ``apply_action('overit_znovu')`` leaves behind for a ``sha_drift``: it re-enters
    Verifikácia as a RE-GATE. Status alone is deliberately not enough (see the busy test below)."""
    version = _seed_settled_version(db_session, state_status="agent_working")
    state = db_session.query(PipelineState).filter_by(version_id=version.id).one()
    state.current_stage = "verifikacia"
    state.is_regate = True
    db_session.flush()
    project = db_session.get(Project, version.project_id)
    owner = db_session.get(User, project.created_by)
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "deadbeef")

    block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

    assert block["cause"] == deploy_service.DEPLOY_CAUSE_REVERIFY_RUNNING
    assert block["version_number"] == version.version_number
    assert block["version_id"] == version.id
    assert block["can_reverify"] is False


def test_deployability_version_busy_when_the_running_turn_is_not_a_reverify(db_session, monkeypatch) -> None:
    """A drifted version that is merely BUSY (a turn in flight that is NOT the re-verification) must NOT be
    announced as "Overujem… odomkne sa samo" — nothing here re-anchors it, so that promise would be false.

    Same status as the test above, WITHOUT the re-gate evidence → ``version_busy``, and still no button
    (``overit_znovu`` fail-closes on any non-settled state, so one would 400)."""
    version = _seed_settled_version(db_session, state_status="agent_working")
    project = db_session.get(Project, version.project_id)
    owner = db_session.get(User, project.created_by)
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "deadbeef")

    block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

    assert block["cause"] == deploy_service.DEPLOY_CAUSE_VERSION_BUSY
    assert block["can_reverify"] is False


def test_deployability_drift_when_the_same_version_is_settled(db_session, monkeypatch) -> None:
    """The pair to the tests above: the SAME fixture, the SAME drift, a SETTLED ``done`` status → ``drift``,
    re-verifiable by the owner. Proves the split really is the pipeline state and not something incidental
    to the drift."""
    version = _seed_settled_version(db_session, state_status="done")
    project = db_session.get(Project, version.project_id)
    owner = db_session.get(User, project.created_by)
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "sha_drift"))
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "deadbeef")

    block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

    assert block["cause"] == deploy_service.DEPLOY_CAUSE_DRIFT
    assert block["version_number"] == version.version_number
    assert block["version_id"] == version.id
    assert block["can_reverify"] is True
