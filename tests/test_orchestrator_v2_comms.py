"""CR-V2-017 — Communications: file-bus retired; direct Manažér ↔ AI Agent + notifications.

Verifies the v2.0.0 communications model (design §5.3) and the orphan-v1 dead-code excision that lands
in this CR:

* the ``.dedo-channel`` file-bus + the hub-and-spoke Coordinator relay are GONE from the engine — no
  ``.dedo-channel`` writer in ``backend/``, and a RAW ``recipient="director"`` / ``author="coordinator"``
  grep on ``orchestrator.py`` is HONESTLY zero;
* the provably-dead v1 Coordinator / autonomy / release-auto functions are excised (no live import breaks);
* the vestigial ``gate_e_dispatch`` param thread is dropped from the dispatch path;
* system → Manažér notifications cover the three design §5.3 events (away / escalation / done): an away
  Manažér is pinged on a build-done (``done``) and on an escalation (``blocked``), and the ping is
  suppressed while the Manažér is actively watching the board.

These are static + behavioural guards so a future edit cannot silently re-introduce the file-bus, the
5-role relay, or a silent autonomous finish.
"""

import inspect
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import notify, orchestrator, pipeline_runner

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
_ORCH = _BACKEND / "services" / "orchestrator.py"


# ─────────────────────────── static excision guards ───────────────────────────


def test_raw_director_coordinator_grep_on_orchestrator_is_zero():
    """The CR-V2-017 honesty gate: the RAW ``recipient="director"`` / ``author="coordinator"`` keyword-arg
    writes (the v1 hub-and-spoke message tokens) are GONE from orchestrator.py — not behind a comment, not
    in a helper, zero. The v2 engine writes ``recipient="manazer"`` / ``author in {ai_agent,auditor,system}``."""
    text = _ORCH.read_text(encoding="utf-8")
    hits = re.findall(r'recipient="director"|author="coordinator"', text)
    assert hits == [], f"orchestrator.py still emits v1 hub-and-spoke tokens: {len(hits)} occurrence(s)"


def test_dedo_channel_file_bus_is_fully_removed_from_backend():
    """The ``.dedo-channel`` file-bus is retired (design §5.3): no source file under ``backend/`` references
    it — no writer, no path literal, no docstring mention that would trip the gate."""
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "dedo-channel" in path.read_text(encoding="utf-8") or "dedo_channel" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(_BACKEND)))
    assert offenders == [], f".dedo-channel still referenced in backend: {offenders}"


@pytest.mark.parametrize(
    "name",
    [
        # Coordinator hub-and-spoke relay / synthesis / executors
        "_coordinator_relay",
        "_coordinator_synthesis",
        "_coordinator_relay_engine_failure",
        "_execute_coordinator_directive",
        "_coordinator_directive_executable",
        "_coordinator_escalate_dedo",
        "_coordinator_route_to_designer",
        "_coordinator_capture_backlog_item",
        "_coordinator_reset_task",
        "_coordinator_move_baseline",
        "_coordinator_clear_session",
        "_coordinator_answer_question",
        "_coordinator_audit",
        "_latest_coordinator_directive",
        # v1 verify-done / retry judge
        "verify_done",
        "_verify_with_retries",
        # v1 autonomy decision family (replaced by the Miera autonómie dial, CR-V2-008)
        "_maybe_autonomous_recovery",
        "_maybe_autonomous_gate_ratify",
        "_maybe_autonomous_answer",
        "_record_autonomous_decision",
        "_record_autonomous_gate",
        "_autonomy_enabled",
        # v1 in-pipeline auto-deploy wrappers (deploy lifted OUT, D6 — CR-V2-026 owns the primitives)
        "_release_auto_publish",
        "_release_auto_uat_deploy",
        "_fast_fix_auto_deploy",
        # internal-turn parse-failure recorder that wrote the retired operator token
        "_record_internal_turn_parse_failure",
    ],
)
def test_dead_v1_symbol_is_excised(name):
    """Each provably-dead v1 Coordinator / autonomy / release-auto symbol is gone from the orchestrator
    module — verify-first proved zero live callers before deletion, so its absence cannot break imports."""
    assert not hasattr(orchestrator, name), f"{name} should have been excised in CR-V2-017"


@pytest.mark.parametrize(
    "name",
    [
        "_EXECUTABLE_COORDINATOR_ACTIONS",
        "_AUTONOMOUS_RECOVERY_ACTIONS",
        "_AUTO_RATIFY_GATES",
        "_DIRECTOR_FORMAT_BRIEF",
        "_FIRST_PRINCIPLES_TRIAGE",
        "_FAST_FIX_RELAY_BRIEF",
    ],
)
def test_dead_v1_constant_is_excised(name):
    """The module constants that only the deleted Coordinator / autonomy functions referenced are gone too
    (no dead data driving nothing)."""
    assert not hasattr(orchestrator, name), f"{name} should have been excised in CR-V2-017"


def test_gate_e_dispatch_param_thread_is_dropped():
    """The vestigial ``gate_e_dispatch`` sub-flow selector is gone from the whole dispatch thread —
    ``run_dispatch`` (orchestrator) + ``schedule_dispatch`` / ``_run`` (runner). The 4-phase model has no
    Gate E (the Auditor's upfront review replaces it)."""
    assert "gate_e_dispatch" not in inspect.signature(orchestrator.run_dispatch).parameters
    assert "gate_e_dispatch" not in inspect.signature(pipeline_runner.schedule_dispatch).parameters
    assert "gate_e_dispatch" not in inspect.signature(pipeline_runner._run).parameters


def test_apply_action_remains_the_sole_state_mutator():
    """Safeguard: the sole-mutator invariant survives the excision — ``apply_action`` is still the public
    state-transition entry point (CR-V2-009), unchanged by the comms cleanup."""
    assert hasattr(orchestrator, "apply_action")
    assert callable(orchestrator.apply_action)


# ─────────────────────────── notification behaviour ───────────────────────────


class _FakeRegistry:
    """Minimal stand-in for the pipeline WS registry presence reads (mirrors the real lock-free API)."""

    def __init__(self):
        self.present: set = set()
        self.away: bool = False

    async def broadcast(self, vid, payload):  # pragma: no cover - unused in notify tests
        pass

    def present_director_ids(self, vid):
        return self.present

    def active_director_ids(self, vid):
        # present AND not away → "actively watching the board" (suppresses the nudge)
        return set() if self.away else self.present

    def away_director_ids(self, vid):
        return self.present if self.away else set()


def _make_version_with_away_owner(db_session, chat_id: str) -> tuple[Version, User]:
    owner = User(
        username=f"mgr_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
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


def _settled_state(db_session, version_id, *, stage: str, status: str) -> PipelineState:
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor="ai_agent",
        status=status,
        next_action="settled",
    )
    db_session.add(state)
    db_session.flush()
    return state


def _capture_telegram(monkeypatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    async def _send(message, chat_id):
        sent.append((message, chat_id))

    monkeypatch.setattr(notify, "send_telegram", _send)
    return sent


def test_done_is_a_notify_worthy_status():
    """CR-V2-017 COMMS-5: ``done`` joins the notify-worthy settles so an autonomously completed build
    (dial=plná, no human stops) still notifies an away Manažér — it must not finish silently."""
    assert "done" in pipeline_runner._NOTIFY_STATUSES
    assert "awaiting_manazer" in pipeline_runner._NOTIFY_STATUSES  # away / you're-up
    assert "blocked" in pipeline_runner._NOTIFY_STATUSES  # escalation


async def test_away_manazer_pinged_on_build_done(db_session, monkeypatch):
    """An away Manažér gets a Telegram on a build-done (``done``) — the 'done' notification event — with the
    distinct 'build hotová' copy (not the generic 'you're up' nudge)."""
    sent = _capture_telegram(monkeypatch)
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)

    version, owner = _make_version_with_away_owner(db_session, chat_id="900900900")
    fake_reg.present = {owner.id}
    fake_reg.away = True  # board open but stepped away
    state = _settled_state(db_session, version.id, stage="done", status="done")

    await pipeline_runner._maybe_notify(db_session, version.id, state)

    assert len(sent) == 1
    assert sent[0][1] == "900900900"
    assert "hotová" in sent[0][0].lower()  # the done-specific copy


async def test_away_manazer_pinged_on_escalation_blocked(db_session, monkeypatch):
    """An away Manažér gets a Telegram on an escalation (``blocked``) — the 'escalation' notification
    event — with the 'you're up' nudge copy."""
    sent = _capture_telegram(monkeypatch)
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)

    version, owner = _make_version_with_away_owner(db_session, chat_id="800800800")
    fake_reg.present = {owner.id}
    fake_reg.away = True
    state = _settled_state(db_session, version.id, stage="verifikacia", status="blocked")

    await pipeline_runner._maybe_notify(db_session, version.id, state)

    assert len(sent) == 1
    assert sent[0][1] == "800800800"
    assert "na rade" in sent[0][0].lower()  # the action-needed nudge copy


async def test_notification_suppressed_when_manazer_actively_watching(db_session, monkeypatch):
    """No out-of-band ping when the Manažér is actively on the board (present AND not away) — the board
    already shows the settle; the Telegram is only for the away / absent case."""
    sent = _capture_telegram(monkeypatch)
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)

    version, owner = _make_version_with_away_owner(db_session, chat_id="700700700")
    fake_reg.present = {owner.id}
    fake_reg.away = False  # actively watching
    state = _settled_state(db_session, version.id, stage="done", status="done")

    await pipeline_runner._maybe_notify(db_session, version.id, state)

    assert sent == []


async def test_no_notification_while_agent_working(db_session, monkeypatch):
    """``agent_working`` is never notify-worthy — only a settle (away / escalation / done) pings."""
    sent = _capture_telegram(monkeypatch)
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)

    version, owner = _make_version_with_away_owner(db_session, chat_id="600600600")
    fake_reg.present = {owner.id}
    fake_reg.away = True
    state = _settled_state(db_session, version.id, stage="programovanie", status="agent_working")

    await pipeline_runner._maybe_notify(db_session, version.id, state)

    assert sent == []


def test_owner_chat_id_resolves_for_absent_manazer(db_session):
    """Audit/notify plumbing: with NO board socket at all (fully absent Manažér) the recipient falls back to
    the project owner's chat_id — the F-007 §9 absent-case path."""
    version, owner = _make_version_with_away_owner(db_session, chat_id="500500500")
    # No registry presence → the absent-Manažér fallback to the project owner.
    chat_ids = pipeline_runner._notify_chat_ids(db_session, version.id)
    assert chat_ids == ["500500500"]
    # And confirm the owner row is wired (the join the notifier uses).
    fetched = db_session.execute(select(User.telegram_chat_id).where(User.id == owner.id)).scalar_one()
    assert fetched == "500500500"
