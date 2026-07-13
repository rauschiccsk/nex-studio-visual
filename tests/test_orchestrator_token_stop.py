"""Spine STEP 1 — the token-stop poistka (REDESIGN §9): a runtime-mutable, honest build stop.

Proves the three legs of the token-stop feature end-to-end against the real v2 branch DB:

1. **The GLOBAL setting** — ``programovanie_token_stop_millions`` is a registered ``int`` default '0'
   (get_int → 0 on a fresh DB), upsert-able (get_int reflects it after the upsert-invalidated cache),
   and type-validated (``'abc'`` → ``ValueError``).
2. **It ACTUALLY pauses** (the spine invariant "must actually pause", NOT cosmetic) — the Programovanie
   build loop, at the task boundary, reads the cap and — when this version's total token spend (the
   append-only log IS the ledger) has crossed it — settles ``paused`` + writes exactly ONE
   ``system→manazer`` notification flagged ``token_stop=True`` and RETURNS before touching the next task.
   Limit 0 = non-stop. Threshold is ``>=`` and counts input+output.
3. **Resume** — from ``paused`` the existing ``pokracovat`` verb re-dispatches, while ask/answer/schvalit
   are rejected (the paused-state guard holds). No new resume path.

Sole-writer / append-only are preserved: the pause writes through ``_record_message`` (the single
constructor site) and the notification carries no ``usage``/``timing`` so it never inflates the ledger.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import notify, orchestrator, pipeline_runner
from backend.services import system_setting as system_setting_service

_KEY = "programovanie_token_stop_millions"

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


@pytest.fixture(autouse=True)
def _clean_setting_cache():
    """The typed-getter cache is process-global + survives SAVEPOINT rollback — clear it around every test
    so an upserted-then-rolled-back value can never leak into another test (metrics-test convention)."""
    system_setting_service._cache.clear()
    orchestrator._RELAY_QUEUES.clear()
    yield
    system_setting_service._cache.clear()
    orchestrator._RELAY_QUEUES.clear()


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_version(db_session):
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
        source_path=None,  # library/no-checkout → _begin_dispatch's _repo_head is a graceful no-op
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_programovanie(db_session, version_id, *, build_dial=None, status="agent_working"):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status=status,
        next_action="working",
        miera_autonomie=build_dial,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_usage(db_session, version_id, *, input_tokens, output_tokens):
    """Record a metered gate_report so ``aggregate_pipeline_usage`` counts these tokens for the version
    (the append-only log IS the token ledger — no separate counter)."""
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="work",
        payload={
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "model": "claude-opus-4-8"},
            "timing": {"duration_seconds": 1.0, "parse_attempts": 1},
        },
    )


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _token_stop_msgs(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("token_stop") is True]


def _set_limit(db_session, millions: str):
    system_setting_service.upsert(db_session, _KEY, millions)


# ── 1) the GLOBAL setting ───────────────────────────────────────────────────────


class TestTokenStopSetting:
    def test_key_registered_default_zero(self, db_session):
        read = system_setting_service.get_by_key(db_session, _KEY)
        assert read.key == _KEY
        assert read.value == "0"
        assert read.value_type == "int"
        assert read.is_default is True
        assert system_setting_service.get_int(db_session, _KEY) == 0  # fresh DB → 0

    def test_upsert_updates_and_cache_invalidated(self, db_session):
        _set_limit(db_session, "3")
        # upsert invalidates the cache → the new value is visible immediately (no 30s wait).
        assert system_setting_service.get_int(db_session, _KEY) == 3

    def test_upsert_invalid_raises(self, db_session):
        with pytest.raises(ValueError):
            _set_limit(db_session, "abc")


# ── 2) it ACTUALLY pauses at the task boundary ──────────────────────────────────


class TestTokenStopPauses:
    async def test_limit_zero_never_pauses(self, db_session, monkeypatch):
        """(d) limit 0 (the default) = non-stop: even with spend well over 1M the seam is a no-op and the
        loop proceeds to the dial-settle (awaiting_manazer), NOT paused, with no token_stop notification."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="po_kazdej_faze")
        _seed_usage(db_session, version.id, input_tokens=2_000_000, output_tokens=0)  # >> 1M, but limit is 0

        # nex-studio-visual: the plan builds at Programovanie entry — this test is a mid-build token check, so
        # the plan is already materialized (skip plan-gen). No tasks → the loop reaches the dial-settle.
        monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)
        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", lambda db, vid: None)
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", lambda db, state: False)

        state = await orchestrator.run_dispatch(db_session, version.id)
        assert state.status == "awaiting_manazer"  # the dial outcome, NOT paused
        assert _token_stop_msgs(db_session, version.id) == []

    async def test_pauses_at_boundary_when_cap_crossed(self, db_session, monkeypatch):
        """(e) limit 1 + spend ≥ 1M → the NEXT task boundary settles paused, writes exactly ONE
        token_stop notification, and RETURNS before get_next_todo_task (proven by making it raise)."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="plna")
        _seed_usage(db_session, version.id, input_tokens=700_000, output_tokens=400_000)  # 1.1M ≥ 1M
        _set_limit(db_session, "1")

        def _must_not_reach(db, vid):  # pragma: no cover - asserts the seam returned first
            raise AssertionError("token-stop must pause BEFORE get_next_todo_task")

        # nex-studio-visual: plan already materialized (mid-build) — skip the Programovanie-entry plan-gen.
        monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)
        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", _must_not_reach)

        state = await orchestrator.run_dispatch(db_session, version.id)
        assert state.status == "paused"
        assert "token-limit" in state.next_action.lower()
        stops = _token_stop_msgs(db_session, version.id)
        assert len(stops) == 1  # exactly one notification
        assert stops[0].author == "system" and stops[0].recipient == "manazer" and stops[0].kind == "notification"
        assert stops[0].payload["tokens_spent"] == 1_100_000
        assert stops[0].payload["limit_millions"] == 1
        # append-only / ledger integrity: the notification carries no usage → it never inflates the count.
        assert "usage" not in stops[0].payload

    async def test_threshold_counts_input_plus_output_at_exact_boundary(self, db_session, monkeypatch):
        """(g) the threshold is ``>=`` and counts input+output: input alone (600k) is below 1M, but
        input+output (600k+400k = exactly 1M) meets the cap → pauses."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="plna")
        _seed_usage(db_session, version.id, input_tokens=600_000, output_tokens=400_000)  # sum == 1M exactly
        _set_limit(db_session, "1")
        monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)
        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", lambda db, vid: None)
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", lambda db, state: False)

        state = await orchestrator.run_dispatch(db_session, version.id)
        assert state.status == "paused"  # exact-equality boundary triggers (>=)

    async def test_input_only_below_cap_does_not_pause(self, db_session, monkeypatch):
        """(g) negative: input-only spend BELOW the cap (900k < 1M, no output) does NOT pause — the loop
        proceeds to the dial-settle. Proves the sum is real (not double-counting) and stays below."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="po_kazdej_faze")
        _seed_usage(db_session, version.id, input_tokens=900_000, output_tokens=0)  # 900k < 1M
        _set_limit(db_session, "1")
        monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)
        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", lambda db, vid: None)
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", lambda db, state: False)

        state = await orchestrator.run_dispatch(db_session, version.id)
        assert state.status == "awaiting_manazer"  # NOT paused
        assert _token_stop_msgs(db_session, version.id) == []

    async def test_zero_token_history_does_not_crash_the_check(self, db_session, monkeypatch):
        """(h) a version with NO metered turns → aggregate is 0 → 0 >= cap is False → no pause, no crash."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="po_kazdej_faze")
        _set_limit(db_session, "1")  # cap set, but there is zero spend on record
        monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)
        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", lambda db, vid: None)
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", lambda db, state: False)

        state = await orchestrator.run_dispatch(db_session, version.id)
        assert state.status == "awaiting_manazer"
        assert _token_stop_msgs(db_session, version.id) == []


# ── 3) resume via the existing verb; paused guard holds ─────────────────────────


class TestResumeFromPaused:
    async def test_pokracovat_resumes_a_token_stop_pause(self, db_session):
        """(f) from a token-stop paused build, ``pokracovat`` re-dispatches (status → agent_working)."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="paused")

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat")
        assert state.status == "agent_working"

    @pytest.mark.parametrize("action", ["ask", "answer", "schvalit"])
    async def test_non_resume_actions_rejected_from_paused(self, db_session, action):
        """(f) the paused-state guard holds: ask/answer/schvalit are rejected from ``paused`` — only
        pokracovat / uprav are valid, so a build can never silently un-pause."""
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="paused")

        with pytest.raises(orchestrator.OrchestratorError):
            await orchestrator.apply_action(
                db_session, version_id=version.id, action=action, payload={"text": "x", "comment": "x"}
            )


# ── 4) Telegram nudge distinguishes the token-stop pause from a manual pause ─────


class _FakeRegistry:
    """Minimal stand-in for the WS registry presence reads (mirrors the real lock-free API)."""

    def __init__(self):
        self.present: set = set()
        self.away: bool = False

    async def broadcast(self, vid, payload):  # pragma: no cover - unused in notify tests
        pass

    def active_director_ids(self, vid):
        return set() if self.away else self.present

    def away_director_ids(self, vid):
        return self.present if self.away else set()


def _make_away_owner_version(db_session, chat_id: str):
    owner = User(
        username=f"mgr_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
        telegram_chat_id=chat_id,
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
        owner_id=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"0.1.{uuid.uuid4().hex[:3]}")
    db_session.add(version)
    db_session.flush()
    return version, owner


def _capture_telegram(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _send(message, chat_id):
        sent.append((message, chat_id))

    monkeypatch.setattr(notify, "send_telegram", _send)
    return sent


def _record_manual_task_note(db_session, version_id):
    """A plain task-boundary note (the shape the loop's last message has when a Manažér manually pauses) —
    NOT a token_stop notification, so ``_is_token_stop_pause`` reads it as a non-token-stop pause."""
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content="▶ Úloha #1 — AI Agent začal.",
        payload={"phase": "programovanie", "task_number": 1},
    )


def _record_token_stop_note(db_session, version_id):
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content="⏸️ Build pozastavený — prekročený token-limit.",
        payload={"phase": "programovanie", "token_stop": True, "tokens_spent": 1_100_000, "limit_millions": 1},
    )


class TestTelegramOnPause:
    def test_paused_is_notify_worthy(self):
        """(m) 'paused' joined the notify-worthy settles alongside the existing three."""
        assert "paused" in pipeline_runner._NOTIFY_STATUSES
        assert {"awaiting_manazer", "blocked", "done"}.issubset(set(pipeline_runner._NOTIFY_STATUSES))

    async def test_token_stop_pause_nudges_away_manazer(self, db_session, monkeypatch):
        sent = _capture_telegram(monkeypatch)
        reg = _FakeRegistry()
        monkeypatch.setattr(pipeline_runner, "registry", reg)
        version, owner = _make_away_owner_version(db_session, chat_id="911911911")
        reg.present, reg.away = {owner.id}, True
        state = _seed_programovanie(db_session, version.id, status="paused")
        _record_token_stop_note(db_session, version.id)  # the token-stop note is the LATEST message

        await pipeline_runner._maybe_notify(db_session, version.id, state)

        assert len(sent) == 1
        assert sent[0][1] == "911911911"
        assert "token-limit" in sent[0][0].lower()

    async def test_manual_pause_does_not_nudge(self, db_session, monkeypatch):
        """The adversarial MINOR fix: a Manažér who paused the build THEMSELVES is not pinged about their
        own action — the latest message is not a token_stop note, so the paused nudge is suppressed."""
        sent = _capture_telegram(monkeypatch)
        reg = _FakeRegistry()
        monkeypatch.setattr(pipeline_runner, "registry", reg)
        version, owner = _make_away_owner_version(db_session, chat_id="922922922")
        reg.present, reg.away = {owner.id}, True
        state = _seed_programovanie(db_session, version.id, status="paused")
        _record_manual_task_note(db_session, version.id)  # NOT a token_stop note

        await pipeline_runner._maybe_notify(db_session, version.id, state)

        assert sent == []  # a manual pause is silent

    async def test_token_stop_pause_silent_when_manazer_present(self, db_session, monkeypatch):
        sent = _capture_telegram(monkeypatch)
        reg = _FakeRegistry()
        monkeypatch.setattr(pipeline_runner, "registry", reg)
        version, owner = _make_away_owner_version(db_session, chat_id="933933933")
        reg.present, reg.away = {owner.id}, False  # actively watching the board
        state = _seed_programovanie(db_session, version.id, status="paused")
        _record_token_stop_note(db_session, version.id)

        await pipeline_runner._maybe_notify(db_session, version.id, state)

        assert sent == []  # on-board → no out-of-band ping

    async def test_existing_awaiting_nudge_still_fires_no_regression(self, db_session, monkeypatch):
        """Regression: the paused gate does not touch the existing awaiting_manazer / blocked / done nudges."""
        sent = _capture_telegram(monkeypatch)
        reg = _FakeRegistry()
        monkeypatch.setattr(pipeline_runner, "registry", reg)
        version, owner = _make_away_owner_version(db_session, chat_id="944944944")
        reg.present, reg.away = {owner.id}, True
        state = _seed_programovanie(db_session, version.id, status="awaiting_manazer")

        await pipeline_runner._maybe_notify(db_session, version.id, state)

        assert len(sent) == 1
        assert "na rade" in sent[0][0].lower()
