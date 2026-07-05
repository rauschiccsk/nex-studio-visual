"""STEP 6 — Hotovo (the Manažér's terminal sign-off) in the conversation register (step6-hotovo-design.md).

After Kontrola, "Označiť ako hotové" (``hotovo``) is the Manažér's TERMINAL signature that makes a conversation
build DEPLOYABLE. Unlike the legacy Auditor path (a ``verdict`` PASS at Verifikácia signed off via ``schvalit``),
a conversation build reaches deployability through THIS manager signature: it settles the build to the terminal
``done`` (verified) stage and records a SHA-anchored marker (``stage='priprava'`` ∧ ``kind='notification'`` ∧
``payload.hotovo``) that :func:`orchestrator.version_verified` honours — the signature REPLACES a verdict (no
verdict is resurrected). Exercised against the real v2 branch DB (4-phase CHECKs). Proves, per the design's
verification plan:

* **(a) trigger gating** — offered ONLY when conversation + spec-approved + kontrola-done + NOT already-done
  (state-only offer + board post-filter + authoritative ``apply_action`` guards).
* **(b) marker** — records ONE ``stage='priprava'`` ∧ ``kind='notification'`` ∧ ``payload.hotovo`` (+ anchored
  ``hotovo_sha``); NOT a verifikacia verdict, so ``_verifikacia_passed`` stays False (INVISIBLE to the release
  gate — the signature is a separate deploy path).
* **(c) terminal settle** — ``current_stage='done'`` / ``status='done'``; NO dispatch (the partner never
  self-signs).
* **(d) deploy-eligibility** — ``hotovo_sha == HEAD`` → ``version_verified`` True ``hotovo_match`` AND
  ``deploy.list_verified_versions`` INCLUDES it; HEAD moved past the signed commit → ``hotovo_drift`` → excluded;
  repo unreadable at sign → ``hotovo_unbound`` (still verified, never anchored).
* **(e) re-open + MD-2 terminal** — a fresh Programovanie-complete outranks the old signature → ``hotovo_done`` /
  ``kontrola_done`` / ``_manazer_signoff`` re-open (honest, stale-aware); a terminal ``done`` board offers
  NOTHING (no UI re-sign — MD-2).
* **(f) legacy + probes** — a legacy (mode NULL) verdict-PASS build has NO signature → ``version_verified`` keys
  off the UNCHANGED verdict path byte-identically; ``hotovo_done`` seq-semantics; the STEP-6
  ``custom_development_enabled`` flag defaults False on an existing project.

No agent turns are involved (``hotovo`` is a pure terminal signature), so no CLI/agent stub is needed; the git
HEAD read + the best-effort tag are stubbed so the SHA-anchor ladder is deterministic without a real repo.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import deploy, orchestrator
from backend.services import system_setting as system_setting_service
from backend.services.orchestrator import OrchestratorError

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


@pytest.fixture(autouse=True)
def _clean_process_state():
    """Clear the process-global relay queues, engine-session set, and the typed-setting cache around every
    test (they survive SAVEPOINT rollback) so nothing leaks between tests."""
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    system_setting_service._cache.clear()
    yield
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    system_setting_service._cache.clear()


# ── fixtures ────────────────────────────────────────────────────────────────


def _make_version(db_session, *, source_path=None):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
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
        source_path=source_path,  # None → _repo_head / doc writers are graceful no-ops
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_priprava(db_session, version_id, *, status="awaiting_manazer", mode="conversation"):
    """A settled conversation build in the priprava register (the SAME shape a spine build carries after Kontrola)."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status=status,
        next_action="rozhovor",
        mode=mode,
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _approve_spec(db_session, version_id):
    """Record the durable kind='approval' Špecifikácia freeze signal (what orchestrator.spec_approved reads)."""
    db_session.add(
        PipelineMessage(
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Špecifikácia schválená.",
            payload={"phase": "priprava", "approve_spec": True},
        )
    )
    db_session.flush()


def _seed_programming_complete(db_session, version_id):
    """Record the STEP-4 MD-B completion notification — what ``programming_complete`` reads (a finished build)."""
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Programovanie dokončené — pokračujeme v rozhovore.",
        payload={"phase": "programovanie", "programming_complete": True},
    )


def _seed_kontrola_report(db_session, version_id):
    """Record the STEP-5 partner gate_report AFTER a build — what ``kontrola_done`` reads (the check ran)."""
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="priprava",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="pevné/vratké",
        payload={"phase": "priprava", "kontrola": True},
    )


def _seed_hotovo_marker(db_session, version_id):
    """Record a raw Hotovo signature marker (what ``hotovo_done`` / ``_manazer_signoff`` read) — for probe tests."""
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="notification",
        content="Označené ako hotové.",
        payload={"phase": "priprava", "hotovo": True, "hotovo_sha": "abc123"},
    )


def _seed_ready_for_hotovo(db_session, version_id, *, mode="conversation"):
    """A conversation build settled at priprava with spec approved + a completed+checked build → Hotovo offerable."""
    state = _seed_priprava(db_session, version_id, mode=mode)
    _approve_spec(db_session, version_id)
    _seed_programming_complete(db_session, version_id)
    _seed_kontrola_report(db_session, version_id)
    return state


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _board_actions(db_session, version_id):
    return pipeline_routes._board(db_session, version_id).available_actions


def _hotovo_markers(db_session, version_id):
    """Manager Hotovo signatures (author=manazer, stage=priprava, kind=notification, payload.hotovo)."""
    return [
        m for m in _msgs(db_session, version_id) if m.kind == "notification" and m.payload and m.payload.get("hotovo")
    ]


def _stub_repo_head(monkeypatch, sha):
    """Pin the repo HEAD SHA (and no-op the best-effort git tag) so the SHA-anchor ladder is deterministic without a
    real git repo. Patched on the orchestrator module — ``deploy.list_verified_versions`` re-imports ``_repo_head``
    from there at call time, so both the ``apply_action`` anchor AND the deploy reality-axis observe the pin."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: sha)
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)


async def _sign_hotovo(db_session, version_id):
    return await orchestrator.apply_action(db_session, version_id=version_id, action="hotovo")


# ── (a) trigger gating: state-only offer + board post-filter + apply_action guards ──


class TestHotovoGating:
    def test_determine_offers_hotovo_at_priprava(self, db_session):
        # State-only (like skontrolovat) — offered UNCONDITIONALLY at a settled priprava.
        version, _ = _make_version(db_session)
        state = _seed_priprava(db_session, version.id)
        assert "hotovo" in orchestrator.determine_available_actions(state)

    def test_board_offers_only_when_conversation_spec_kontrola_not_done(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        # no spec, no build → post-filtered out.
        assert "hotovo" not in _board_actions(db_session, version.id)
        _approve_spec(db_session, version.id)
        # spec approved but no completed+checked build → still out.
        assert "hotovo" not in _board_actions(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        # build complete but Kontrola NOT run yet → still out.
        assert "hotovo" not in _board_actions(db_session, version.id)
        _seed_kontrola_report(db_session, version.id)
        # conversation + spec + kontrola done + not done → OFFERED.
        assert "hotovo" in _board_actions(db_session, version.id)

    def test_board_hides_hotovo_on_legacy_build(self, db_session):
        # mode NULL → determine still offers it (state-only), but the board post-filter drops it (not conversation).
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id, mode=None)
        assert "hotovo" not in _board_actions(db_session, version.id)

    async def test_apply_raises_when_not_conversation(self, db_session):
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id, mode=None)
        with pytest.raises(OrchestratorError, match="rozhovorovom"):
            await _sign_hotovo(db_session, version.id)

    async def test_apply_raises_when_spec_not_approved(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _seed_kontrola_report(db_session, version.id)  # kontrola present but NO spec approval
        with pytest.raises(OrchestratorError, match="schválení Špecifikácie"):
            await _sign_hotovo(db_session, version.id)

    async def test_apply_raises_when_kontrola_not_done(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)  # build done but NO kontrola report
        with pytest.raises(OrchestratorError, match="po Kontrole"):
            await _sign_hotovo(db_session, version.id)

    async def test_apply_raises_when_already_done(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")
        await _sign_hotovo(db_session, version.id)  # first signature → terminal done
        # MD-2: a re-sign is refused — the terminal state itself blocks it (no UI re-open in STEP 6).
        with pytest.raises(OrchestratorError, match="už hotová"):
            await _sign_hotovo(db_session, version.id)


# ── (b) marker: ONE priprava notification carrying payload.hotovo — NOT a verdict ──


class TestHotovoMarker:
    async def test_records_notification_marker_not_verdict(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")

        await _sign_hotovo(db_session, version.id)

        markers = _hotovo_markers(db_session, version.id)
        assert len(markers) == 1  # EXACTLY one signature
        m = markers[0]
        assert m.stage == "priprava" and m.kind == "notification"
        assert m.author == "manazer" and m.recipient == "ai_agent"
        assert m.payload.get("hotovo") is True and m.payload.get("hotovo_sha") == "abc123"
        # NOT a verifikacia verdict → the release gate stays closed (the signature is a SEPARATE deploy path).
        assert orchestrator._verifikacia_passed(db_session, version.id) is False
        # and NO verifikacia-stage message was ever created by the signature.
        assert [x for x in _msgs(db_session, version.id) if x.stage == "verifikacia"] == []


# ── (c) terminal settle: done / done, no dispatch (mirror schvalit done-branch) ──


class TestHotovoTerminalSettle:
    async def test_settles_done_no_dispatch(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")

        def _boom_dispatch(*a, **k):  # pragma: no cover
            raise AssertionError("hotovo must not dispatch an agent turn (the partner never self-signs)")

        monkeypatch.setattr(orchestrator, "_begin_dispatch", _boom_dispatch)

        state = await _sign_hotovo(db_session, version.id)
        assert state.current_stage == "done"  # terminal verified stage (mirror schvalit done-branch)
        assert state.status == "done"
        assert state.current_actor == "ai_agent"  # terminal — a valid ACTOR value, no agent on turn
        assert "nasadenie" in state.next_action.lower()


# ── (d) deploy-eligibility: hotovo_match INCLUDED, drift EXCLUDED, unbound INCLUDED ──


class TestHotovoDeployEligibility:
    async def test_hotovo_match_is_deployable(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")

        await _sign_hotovo(db_session, version.id)

        verified, provenance = orchestrator.version_verified(db_session, version.id)
        assert verified is True and provenance == "hotovo_match"
        # candidate axis (current_stage=='done') + reality axis (signoff) → in the deployable list.
        assert version.version_number in deploy.list_verified_versions(db_session, project.id)

    async def test_head_drift_auto_un_verifies_and_excludes(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")
        await _sign_hotovo(db_session, version.id)
        # HEAD moves PAST the signed commit → auto-un-verify (no frozen signature).
        monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "def456")
        verified, provenance = orchestrator.version_verified(db_session, version.id)
        assert verified is False and provenance == "hotovo_drift"
        assert version.version_number not in deploy.list_verified_versions(db_session, project.id)

    async def test_unbound_when_repo_unreadable_at_sign(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, None)  # repo unreadable at sign → the signature never got a SHA anchor
        await _sign_hotovo(db_session, version.id)
        verified, provenance = orchestrator.version_verified(db_session, version.id)
        assert verified is True and provenance == "hotovo_unbound"  # do NOT un-verify an unanchored signature
        assert version.version_number in deploy.list_verified_versions(db_session, project.id)


# ── (e) honest re-open + MD-2 terminal: stale-aware probes; done board offers nothing ──


class TestHotovoReopenAndTerminal:
    async def test_fresh_build_reopens_and_drops_from_deployable(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")
        await _sign_hotovo(db_session, version.id)
        assert orchestrator.hotovo_done(db_session, version.id) is True
        assert version.version_number in deploy.list_verified_versions(db_session, project.id)

        # a NEW build completes (fresher seq) → the old signature + kontrola go STALE (honest re-open).
        _seed_programming_complete(db_session, version.id)
        assert orchestrator.kontrola_done(db_session, version.id) is False
        assert orchestrator.hotovo_done(db_session, version.id) is False
        assert orchestrator._manazer_signoff(db_session, version.id) is None
        # stale signature → version_verified falls through to the verdict path (no verdict) → not verified → dropped.
        assert orchestrator.version_verified(db_session, version.id)[0] is False
        assert version.version_number not in deploy.list_verified_versions(db_session, project.id)

    async def test_md2_terminal_board_offers_nothing(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_ready_for_hotovo(db_session, version.id)
        _stub_repo_head(monkeypatch, "abc123")
        await _sign_hotovo(db_session, version.id)
        # MD-2 TERMINAL: a done conversation build offers NOTHING — no UI re-sign (honest, out of STEP 6 scope).
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "done" and state.current_stage == "done"
        assert orchestrator.determine_available_actions(state) == set()
        assert _board_actions(db_session, version.id) == []


# ── (f) legacy verdict path byte-identical + probes + STEP-6 flag default ──


class TestLegacyAndProbes:
    def test_legacy_verdict_path_unchanged(self, db_session, monkeypatch):
        # A legacy build with a real Auditor PASS verdict anchored to a SHA, NO hotovo signature → _manazer_signoff
        # is None → version_verified keys off the UNCHANGED verdict path (sha_match / sha_drift, NOT hotovo_*).
        version, _ = _make_version(db_session)
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="verifikacia",
                author="auditor",
                recipient="manazer",
                kind="verdict",
                content="PASS",
                payload={"verdict": "PASS", "verified_sha": "abc123"},
            )
        )
        db_session.flush()
        assert orchestrator._manazer_signoff(db_session, version.id) is None  # no signature → legacy path
        monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "abc123")
        assert orchestrator.version_verified(db_session, version.id) == (True, "sha_match")
        monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "zzz999")
        assert orchestrator.version_verified(db_session, version.id) == (False, "sha_drift")

    def test_hotovo_done_seq_semantics(self, db_session):
        version, _ = _make_version(db_session)
        # no build → not done.
        assert orchestrator.hotovo_done(db_session, version.id) is False
        _seed_programming_complete(db_session, version.id)
        # build complete but no signature yet → not done.
        assert orchestrator.hotovo_done(db_session, version.id) is False
        _seed_hotovo_marker(db_session, version.id)
        # signature AFTER the build → done.
        assert orchestrator.hotovo_done(db_session, version.id) is True
        # a fresher build completion outranks the old signature → re-opened.
        _seed_programming_complete(db_session, version.id)
        assert orchestrator.hotovo_done(db_session, version.id) is False

    def test_custom_development_enabled_defaults_false(self, db_session):
        # STEP-6 flag: the server_default backfills existing / newly-created projects to False (no custom-dev
        # project exists yet), so an existing project is byte-identical.
        _, project = _make_version(db_session)
        db_session.refresh(project)
        assert project.custom_development_enabled is False
