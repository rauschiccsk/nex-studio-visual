"""STEP 5 — Kontrola (honest self-check) in the conversation register (step5-kontrola-design.md).

After Programovanie completes, "Skontrolovať" (``skontrolovat``) runs the partner's HONEST self-check of its
OWN work — the engine boots the app in an ephemeral isolated stack + runs acceptance FIRST (honesty proof),
then the SAME AI Agent (NOT the Auditor) reconciles with that result against the approved ``specification.md``
and reports PEVNÉ / VRATKÉ as ONE ordinary conversation message (``kind='gate_report'`` — NEVER a
``verdict``). The round STAYS at ``current_stage='priprava'`` so it is INVISIBLE to the release/deploy path
(a verdict at ``verifikacia`` reads as a release PASS). Exercised against the real v2 branch DB (4-phase
CHECKs). Proves, per the design's verification plan:

* **(a) trigger gating** — offered ONLY when conversation + spec-approved + programming-complete + NOT
  already-checked (state-only offer + board post-filter + authoritative ``apply_action`` guards).
* **(b) apply_action** — records a durable ``check`` marker, arms ``agent_working``, STAYS at ``priprava``.
* **(c) the round** — records both smoke legs ``system→manazer`` at ``stage='priprava'`` BEFORE the partner
  turn, then ONE ``kind='gate_report'`` with ``payload.kontrola`` at ``stage='priprava'`` (NOT verdict /
  verifikacia); settles ``awaiting_manazer`` with ``current_stage`` unchanged, and NEVER calls
  ``_settle_phase_boundary`` / ``_next_stage``.
* **(d) runtime floor red** — a red boot / red acceptance records a ``kontrola_floor_red`` notification and
  still settles ``awaiting_manazer`` (K-3 = NO auto-fix loop).
* **(e) invisibility / safety** — the kontrola gate_report is invisible to ``_verifikacia_passed`` /
  ``version_verified`` / ``deploy.list_verified_versions``; a real Verifikácia verdict is still visible
  (legacy Auditor path byte-identical); a second kontrola is refused; a new build re-opens it.
* **(f) probes** — ``programming_complete`` / ``kontrola_done`` compute from the message log.
* **(g) failure settles** — ParseFailure → blocked/parse_exhaustion; question → blocked/agent_question.

The smoke is STUBBED (no real docker); ``invoke_agent_with_parse_retry`` is stubbed to record the partner's
gate_report exactly as the real ``invoke_agent`` chokepoint would, so the recorded message is assertable.
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
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

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
    """A settled conversation build in the priprava register (the SAME shape a spine build carries)."""
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


def _kontrola_reports(db_session, version_id):
    """Partner gate_reports carrying the kontrola marker (author=ai_agent, stage=priprava)."""
    return [
        m for m in _msgs(db_session, version_id) if m.kind == "gate_report" and m.payload and m.payload.get("kontrola")
    ]


def _floor_red_notes(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("kontrola_floor_red")]


# ── stubs (no live docker, no live claude CLI) ────────────────────────────────


def _gate_report_block(summary="pevné/vratké"):
    return PipelineStatusBlock(stage="priprava", kind="gate_report", summary=summary, awaiting="manazer")


def _question_block(question="Ktorý scenár mám overiť?"):
    return PipelineStatusBlock(
        stage="priprava", kind="question", summary="neistota", awaiting="manazer", question=question
    )


def _stub_smoke(monkeypatch, *, boot_ok=True, boot_detail="ok", acceptance=(True, "acc ok", False)):
    """Stub ``_run_release_smoke`` — return a scripted ((boot_ok, detail), acceptance) WITHOUT docker.
    A red boot passes ``acceptance=None`` (acceptance never ran), mirroring the real driver."""

    async def _fake(project_slug, version_label, coverage_req=(0, 0)):
        return (boot_ok, boot_detail), acceptance

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _fake)


def _stub_partner_turn(monkeypatch, block):
    """Stub ``invoke_agent_with_parse_retry`` to RECORD a message the way the real ``invoke_agent`` chokepoint
    does (so the recorded gate_report is assertable) and return the block. A ParseFailure records nothing (the
    real path returns it un-recorded; the caller records the parse-exhaustion note)."""
    calls: list[dict] = []

    async def _fake(db, *, version_id, role, stage, prompt, recipient="manazer", extra_payload=None, **_kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt, "recipient": recipient})
        if isinstance(block, ParseFailure):
            return block
        msg_kind = "question" if block.kind in ("question", "blocked") else block.kind
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage=stage,
            author=role,
            recipient=recipient,
            kind=msg_kind,
            content=block.summary or "",
            payload={**(extra_payload or {})},
        )
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


async def _drive_kontrola(db_session, version_id):
    """Apply ``skontrolovat`` (records the marker + arms working) then run the delegated conversation turn —
    the full runner path (``run_conversation_turn`` → ``_run_conversation_kontrola_round``)."""
    st = await orchestrator.apply_action(db_session, version_id=version_id, action="skontrolovat")
    assert st.status == "agent_working"
    return await orchestrator.run_conversation_turn(db_session, version_id)


# ── (a) trigger gating: state-only offer + board post-filter + apply_action guards ──


class TestSkontrolovatGating:
    def test_determine_offers_skontrolovat_at_priprava(self, db_session):
        # State-only (like zostav_plan / spustit_stavbu) — offered UNCONDITIONALLY at a settled priprava.
        version, _ = _make_version(db_session)
        state = _seed_priprava(db_session, version.id)
        assert "skontrolovat" in orchestrator.determine_available_actions(state)

    def test_board_offers_only_when_conversation_spec_complete_not_checked(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        # not spec-approved, no build → post-filtered out.
        assert "skontrolovat" not in _board_actions(db_session, version.id)
        _approve_spec(db_session, version.id)
        # spec approved but Programovanie NOT complete → still out.
        assert "skontrolovat" not in _board_actions(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        # conversation + spec + programming complete + not checked → OFFERED.
        assert "skontrolovat" in _board_actions(db_session, version.id)

    def test_board_hides_skontrolovat_on_legacy_build(self, db_session):
        # mode NULL → determine still offers it (state-only), but the board post-filter drops it (not conversation).
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        assert "skontrolovat" not in _board_actions(db_session, version.id)

    async def test_apply_raises_when_not_conversation(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        with pytest.raises(OrchestratorError, match="rozhovorovom"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="skontrolovat")

    async def test_apply_raises_when_spec_not_approved(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _seed_programming_complete(db_session, version.id)  # complete but NO spec approval
        with pytest.raises(OrchestratorError, match="schválení Špecifikácie"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="skontrolovat")

    async def test_apply_raises_when_programming_not_complete(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)  # spec but NO completed build
        with pytest.raises(OrchestratorError, match="dokončení Programovania"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="skontrolovat")


# ── (b) apply_action arms the round + durable check marker (stays priprava) ───


class TestSkontrolovatArmsRound:
    async def test_apply_arms_working_records_marker_stays_priprava(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="skontrolovat")

        assert state.status == "agent_working"  # _begin_dispatch armed the turn
        assert state.current_stage == "priprava"  # STAYS priprava — NOT moved (mirror zostav_plan, not spustit_stavbu)
        assert state.mode == "conversation"
        assert state.current_actor == "ai_agent"
        marker = _msgs(db_session, version.id)[-1]
        assert marker.kind == "directive" and marker.author == "manazer" and marker.recipient == "ai_agent"
        assert marker.payload.get("check") is True and marker.stage == "priprava"
        # the marker is a check directive, NOT a compose_plan directive (never mis-routes to the plan round).
        assert orchestrator._pending_check_marker(db_session, version.id) is True
        assert orchestrator._pending_compose_plan_marker(db_session, version.id) is False


# ── (c) the round: smoke legs first, ONE gate_report at priprava, no phase walk ──


class TestKontrolaRound:
    async def test_round_records_smoke_then_gate_report_at_priprava(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch, boot_ok=True, acceptance=(True, "3 assertions", False))
        _stub_partner_turn(monkeypatch, _gate_report_block())

        state = await _drive_kontrola(db_session, version.id)

        assert state.status == "awaiting_manazer"
        assert state.current_stage == "priprava"  # UNCHANGED — kontrola never walks the phase automaton
        assert state.mode == "conversation"
        # ONE partner gate_report, carrying payload.kontrola, at stage='priprava', from the AI Agent (not auditor).
        reports = _kontrola_reports(db_session, version.id)
        assert len(reports) == 1
        r = reports[0]
        assert r.stage == "priprava" and r.author == "ai_agent" and r.recipient == "manazer"
        assert r.kind == "gate_report"  # NEVER a verdict
        # both smoke legs recorded system→manazer at stage='priprava' BEFORE the partner report (lower seq).
        smoke_notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("smoke")]
        acc_notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("release_acceptance")]
        assert len(smoke_notes) == 1 and smoke_notes[0].stage == "priprava" and smoke_notes[0].author == "system"
        assert len(acc_notes) == 1 and acc_notes[0].stage == "priprava"
        assert smoke_notes[0].seq < r.seq and acc_notes[0].seq < r.seq  # proof recorded BEFORE the partner spoke
        # green run → no floor-red note.
        assert _floor_red_notes(db_session, version.id) == []

    async def test_partner_turn_role_is_ai_agent_not_auditor(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        calls = _stub_partner_turn(monkeypatch, _gate_report_block())

        await _drive_kontrola(db_session, version.id)

        assert len(calls) == 1
        assert calls[0]["role"] == "ai_agent"  # the SAME partner, NOT the independent Auditor
        assert calls[0]["role"] != orchestrator.AUDITOR_ROLE
        assert calls[0]["stage"] == "priprava" and calls[0]["recipient"] == "manazer"

    async def test_round_never_calls_settle_or_next_stage(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        _stub_partner_turn(monkeypatch, _gate_report_block())

        def _boom_settle(*a, **k):  # pragma: no cover
            raise AssertionError("kontrola must not call _settle_phase_boundary")

        def _boom_next(*a, **k):  # pragma: no cover
            raise AssertionError("kontrola must not call _next_stage")

        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _boom_settle)
        monkeypatch.setattr(orchestrator, "_next_stage", _boom_next)

        state = await _drive_kontrola(db_session, version.id)
        assert state.current_stage == "priprava" and state.status == "awaiting_manazer"


# ── (d) runtime floor red: honest floor note + settle, NO auto-fix loop (K-3) ──


class TestRuntimeFloorRed:
    async def test_red_boot_records_floor_note_and_settles(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch, boot_ok=False, boot_detail="up exit 1", acceptance=None)
        _stub_partner_turn(monkeypatch, _gate_report_block("appka nenaštartovala — VRATKÉ"))

        state = await _drive_kontrola(db_session, version.id)

        assert state.status == "awaiting_manazer"  # K-3: settle + hand back — NO auto-fix loop
        assert state.current_stage == "priprava"
        floor = _floor_red_notes(db_session, version.id)
        assert len(floor) == 1 and floor[0].author == "system" and floor[0].stage == "priprava"
        # the partner turn still ran + produced ONE gate_report (it reconciles honestly with the red machine).
        assert len(_kontrola_reports(db_session, version.id)) == 1

    async def test_red_acceptance_records_floor_note(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        # boot OK but acceptance RAN and did NOT pass (not skipped) → floored red.
        _stub_smoke(monkeypatch, boot_ok=True, acceptance=(False, "1 assertion failed", False))
        _stub_partner_turn(monkeypatch, _gate_report_block())

        state = await _drive_kontrola(db_session, version.id)
        assert state.status == "awaiting_manazer"
        assert len(_floor_red_notes(db_session, version.id)) == 1

    async def test_skipped_acceptance_is_not_floor_red(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        # boot OK, acceptance SKIPPED (pure lib / no smoke script) — a SKIP is NOT red.
        _stub_smoke(monkeypatch, boot_ok=True, acceptance=(True, "SKIPPED — no script", True))
        _stub_partner_turn(monkeypatch, _gate_report_block())

        await _drive_kontrola(db_session, version.id)
        assert _floor_red_notes(db_session, version.id) == []


# ── (e) invisibility / safety: release gate never sees kontrola; second refused; re-open ──


class TestInvisibilityAndSafety:
    async def test_kontrola_invisible_to_verifikacia_gates(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        _stub_partner_turn(monkeypatch, _gate_report_block())

        await _drive_kontrola(db_session, version.id)

        # a kontrola gate_report at priprava is INVISIBLE to the release gates.
        assert orchestrator._verifikacia_passed(db_session, version.id) is False
        assert orchestrator.version_verified(db_session, version.id)[0] is False
        # and never a deployable version (it stays at priprava, never reaches the 'done' verified stage).
        assert version.version_number not in deploy.list_verified_versions(db_session, project.id)

    def test_verdict_still_visible_kontrola_ignored(self, db_session):
        # Legacy Auditor path byte-identical: a real Verifikácia verdict PASS is still seen; a priprava kontrola
        # gate_report never interferes with the release gate.
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="priprava",
                author="ai_agent",
                recipient="manazer",
                kind="gate_report",
                content="pevné/vratké",
                payload={"phase": "priprava", "kontrola": True},
            )
        )
        db_session.flush()
        assert orchestrator._verifikacia_passed(db_session, version.id) is False  # kontrola report ignored
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="verifikacia",
                author="auditor",
                recipient="manazer",
                kind="verdict",
                content="PASS",
                payload={"verdict": "PASS"},
            )
        )
        db_session.flush()
        assert orchestrator._verifikacia_passed(db_session, version.id) is True  # real verdict path untouched

    async def test_second_kontrola_refused_new_build_reopens(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        _stub_partner_turn(monkeypatch, _gate_report_block())

        await _drive_kontrola(db_session, version.id)
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        state.status = "awaiting_manazer"  # settle so the next apply_action is not blocked by in-flight
        state.dispatch_in_flight = False
        db_session.flush()

        # K-4: one kontrola per completed build — a second is refused, and the board drops the button.
        assert orchestrator.kontrola_done(db_session, version.id) is True
        assert "skontrolovat" not in _board_actions(db_session, version.id)
        with pytest.raises(OrchestratorError, match="už prebehla"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="skontrolovat")

        # a NEW build completion (fresher seq) re-opens the check.
        _seed_programming_complete(db_session, version.id)
        assert orchestrator.kontrola_done(db_session, version.id) is False
        assert "skontrolovat" in _board_actions(db_session, version.id)


# ── (f) probes ────────────────────────────────────────────────────────────────


class TestProbes:
    def test_programming_complete(self, db_session):
        version, _ = _make_version(db_session)
        assert orchestrator.programming_complete(db_session, version.id) is False
        _seed_programming_complete(db_session, version.id)
        assert orchestrator.programming_complete(db_session, version.id) is True

    def test_kontrola_done_seq_semantics(self, db_session):
        version, _ = _make_version(db_session)
        # no build → not done.
        assert orchestrator.kontrola_done(db_session, version.id) is False
        _seed_programming_complete(db_session, version.id)
        # build complete but no kontrola report yet → not done.
        assert orchestrator.kontrola_done(db_session, version.id) is False
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="priprava",
                author="ai_agent",
                recipient="manazer",
                kind="gate_report",
                content="kontrola",
                payload={"phase": "priprava", "kontrola": True},
            )
        )
        db_session.flush()
        # kontrola report AFTER the build → done.
        assert orchestrator.kontrola_done(db_session, version.id) is True
        # a fresher build completion outranks the old kontrola report → re-opened.
        _seed_programming_complete(db_session, version.id)
        assert orchestrator.kontrola_done(db_session, version.id) is False


# ── (g) failure settles: parse-exhaustion + agent question ────────────────────


class TestFailureSettles:
    async def test_parse_failure_settles_blocked_parse_exhaustion(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        _stub_partner_turn(monkeypatch, ParseFailure("no PIPELINE_STATUS block found"))

        state = await _drive_kontrola(db_session, version.id)

        assert state.status == "blocked" and state.block_reason == "parse_exhaustion"
        assert state.current_stage == "priprava"
        # no kontrola gate_report was recorded (parse failed); a readable parse-exhaustion note is on the log.
        assert _kontrola_reports(db_session, version.id) == []
        exhaustion = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("parse_failure_reason")]
        assert len(exhaustion) == 1 and exhaustion[0].author == "system"

    async def test_question_settles_blocked_agent_question(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_programming_complete(db_session, version.id)
        _stub_smoke(monkeypatch)
        _stub_partner_turn(monkeypatch, _question_block("Ktorý scenár mám overiť?"))

        state = await _drive_kontrola(db_session, version.id)

        assert state.status == "blocked" and state.block_reason == "agent_question"
        assert state.current_stage == "priprava"
        assert "Ktorý scenár" in state.next_action
