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
