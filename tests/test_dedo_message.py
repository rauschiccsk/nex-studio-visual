"""ICCINT-12 — Dedo can answer the AI Agent, and the answer actually reaches it.

The AI Agent could already escalate to Dedo (``block_reason='framework_issue'`` → the .dedo-channel inbox
file + a Telegram ping). The way back did not exist: the participant CHECK rejected ``author='dedo'``, so a
human had to retype Dedo's answer into the Manažér's box.

These tests assert the BEHAVIOUR of the return leg, not its shape:

1. **The service writes Dedo's voice as Dedo's** — author ``dedo``, addressed to ``ai_agent``, ``pending``
   until delivered, stamped with the phase the build is actually in; and it REFUSES to write an empty
   message or one aimed at a version with no build.
2. **The message reaches what the agent gets** — the real chain (``run_conversation_turn`` →
   ``invoke_agent_with_parse_retry`` → ``invoke_agent`` → ``invoke_claude``) is exercised with only the
   headless CLI faked, and the prompt handed to that CLI must contain Dedo's text. This is the whole point
   of the change: a message the agent never sees is a failed delivery, not a partial one.
3. **Delivered once, and never by the wrong turn** — the next turn does not repeat it; an Auditor turn does
   not swallow a message addressed to the AI Agent; the read-only Konzultácia turn does not either (driven
   through ``run_consult_turn``, so it proves Konzultácia SENDS the flag, not merely that the flag works);
   and a turn whose ENVELOPE IS LOST — the way a real crash/timeout actually presents, as a ``ParseFailure``
   from ``invoke_agent``, not as a raised exception — leaves it pending and the next turn carries it.
4. **The build resolution Dedo replies through** — ``version_awaiting_dedo`` finds the build blocked on
   ``framework_issue`` and refuses to guess when the answer is ambiguous.
5. **The trigger** — pinned from both sides: a ``framework_issue`` block neither OFFERS nor EXECUTES any
   action that runs an agent turn (so the Manažér can never release it himself — asserted by calling each
   verb, and the chat relay too, not by reading the menu), and Dedo's ICCINT-13 unblock arms exactly one
   that does.
6. **The host CLI** — the only way back today, exercised end-to-end including the commit.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.cli import dedo_message as cli_dedo
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import dedo_message, dedo_unblock, orchestrator
from backend.services.claude_agent import ClaudeAgentError, ClaudeAgentTimeout

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

_DEDO_TEXT = "Opravené v NEX Studiu v4.0.58 — chybu s uzamknutým portom už nemáš, skús ten build znova."


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_version(db_session, *, slug: str | None = None):
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
        slug=slug or f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_state(db_session, version_id, **overrides) -> PipelineState:
    defaults = {
        "version_id": version_id,
        "flow_type": "new_version",
        "current_stage": "priprava",
        "current_actor": "ai_agent",
        "status": "agent_working",
        "next_action": "rozhovor",
        "mode": "conversation",
    }
    defaults.update(overrides)
    state = PipelineState(**defaults)
    db_session.add(state)
    db_session.flush()
    return state


def _block(kind="answer", summary="pokračujem", stage="priprava") -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": "manazer"}
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _fake_cli(monkeypatch, *, response=None):
    """Fake the headless ``claude`` seam and capture every prompt the agent is actually handed."""
    prompts: list[str] = []

    async def _fake(*, prompt, **_kw):
        prompts.append(prompt)
        return response if response is not None else _block()

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    return prompts


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


# ── 1. the write ──────────────────────────────────────────────────────────────


class TestRecordDedoMessage:
    def test_records_as_dedo_addressed_to_the_agent_and_pending(self, db_session):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, current_stage="programovanie", status="blocked")

        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=f"  {_DEDO_TEXT}  ")

        assert msg.author == "dedo"
        assert msg.recipient == "ai_agent"
        assert msg.content == _DEDO_TEXT  # trimmed
        # Undelivered until a turn carries it — this is what makes the delivery at-least-once.
        assert msg.status == "pending"
        # Stamped with the phase the build is actually sitting in, so it lands in the right part of the thread.
        assert msg.stage == "programovanie"
        assert msg.payload["phase"] == "programovanie"

        # It is a real row in the append-only log, readable back like any other message.
        rows = _msgs(db_session, version.id)
        assert [(m.author, m.recipient) for m in rows] == [("dedo", "ai_agent")]

    def test_refuses_an_empty_message(self, db_session):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        with pytest.raises(dedo_message.DedoMessageError):
            dedo_message.record_dedo_message(db_session, version_id=version.id, content="   \n ")
        assert _msgs(db_session, version.id) == []

    def test_refuses_a_version_with_no_build(self, db_session):
        """No pipeline → there is no agent to answer; writing anyway would file the message where nothing reads."""
        version, _ = _make_version(db_session)
        with pytest.raises(dedo_message.DedoMessageError):
            dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)


# ── 2./3. the delivery ────────────────────────────────────────────────────────


class TestDeliveryToTheAgent:
    async def test_message_lands_in_the_prompt_the_agent_is_handed(self, db_session, monkeypatch):
        """The point of the whole change: Dedo's text is in what the agent actually receives."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, version.id)

        assert len(prompts) == 1
        assert _DEDO_TEXT in prompts[0]
        # Named as Dedo — the agent must not read it as the Manažér's instruction (a Manažér cannot fix
        # NEX Studio, which is exactly why the build was escalated).
        assert "Dedo" in prompts[0]
        # PREPENDED, not substituted: the turn's own brief survives underneath.
        assert "Pokračuj v živom rozhovore" in prompts[0]
        assert prompts[0].index(_DEDO_TEXT) < prompts[0].index("Pokračuj v živom rozhovore")

    async def test_delivered_exactly_once(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, version.id)
        db_session.refresh(msg)
        assert msg.status == "delivered"

        # A second turn must not repeat it — a delivered answer replayed on every turn would read as a new
        # instruction each time. (The turn above settled the state; re-arm it.)
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        state.status = "agent_working"
        db_session.flush()
        await orchestrator.run_conversation_turn(db_session, version.id)

        assert len(prompts) == 2
        assert _DEDO_TEXT not in prompts[1]

    @pytest.mark.parametrize(
        "crash",
        [ClaudeAgentError("claude exited 1 — connection lost"), ClaudeAgentTimeout("wall-clock budget burned")],
        ids=["crash", "timeout"],
    )
    async def test_a_lost_envelope_does_not_consume_it_and_the_next_turn_carries_it(
        self, db_session, monkeypatch, crash
    ):
        """The real loss path: the headless ``claude`` dies, so the prompt never reached the model.

        This is what a crashed turn ACTUALLY looks like — ``invoke_agent`` catches ``ClaudeAgentError`` /
        ``ClaudeAgentTimeout`` and RETURNS a ``ParseFailure``; it does not raise. The message must survive
        that turn and ride the NEXT one, otherwise the build round's crash auto-retry re-runs without it and
        Dedo's single answer to the escalation is gone with no trace on the board.
        """
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)

        async def _dies(**_kw):
            raise crash

        monkeypatch.setattr(orchestrator, "invoke_claude", _dies)
        await orchestrator.run_conversation_turn(db_session, version.id)

        db_session.refresh(msg)
        assert msg.status == "pending"

        # …and it is genuinely re-delivered: the next turn that DOES reach the agent carries the text.
        prompts = _fake_cli(monkeypatch)
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        state.status = "agent_working"
        db_session.flush()
        await orchestrator.run_conversation_turn(db_session, version.id)

        assert prompts and _DEDO_TEXT in prompts[0]
        db_session.refresh(msg)
        assert msg.status == "delivered"

    async def test_an_ordinary_parse_failure_still_counts_as_delivered(self, db_session, monkeypatch):
        """The opposite case, so the fix above is not over-broad: an envelope CAME BACK, it just had no
        valid status block. The agent read the prompt, so the message is spent — replaying it on the cheap
        re-emit retries (or on the next turn) would read as a brand-new instruction."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)
        prompts = _fake_cli(monkeypatch, response="Skončil som, ale stavový blok neposielam.")

        await orchestrator.run_conversation_turn(db_session, version.id)

        assert _DEDO_TEXT in prompts[0]  # the agent did get it
        db_session.refresh(msg)
        assert msg.status == "delivered"
        # The re-emit retries ask only for a corrected block — they must not repeat Dedo's instruction.
        assert all(_DEDO_TEXT not in p for p in prompts[1:])

    async def test_an_auditor_turn_does_not_swallow_it(self, db_session, monkeypatch):
        """The message is addressed to the AI Agent. The Auditor verifies the project — it is not his to answer."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, current_stage="verifikacia", current_actor="auditor")
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)
        prompts = _fake_cli(monkeypatch)

        await orchestrator.invoke_agent_with_parse_retry(
            db_session,
            version_id=version.id,
            role="auditor",
            stage="verifikacia",
            prompt="Audítorský brief.",
        )

        assert prompts and _DEDO_TEXT not in prompts[0]
        db_session.refresh(msg)
        assert msg.status == "pending"  # still waiting for the AI Agent's next turn

    async def test_the_read_only_consult_turn_does_not_swallow_it(self, db_session, monkeypatch):
        """Konzultácia advises the Manažér on a finished build with no write tools — it cannot act on a
        Dedo instruction, so consuming it there would lose it silently.

        Driven through :func:`run_consult_turn` — the real entry point — NOT through the shared chokepoint
        with the flag handed in by the test. Passing ``deliver_dedo=False`` here ourselves would only prove
        the parameter works when someone sends it, which is not the property under test: the property is
        that Konzultácia SENDS it. (Verified by mutation: deleting ``deliver_dedo=False`` from
        ``run_consult_turn`` must turn this test red.)
        """
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, current_stage="done", status="agent_working", next_action="konzultacia")
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)
        prompts = _fake_cli(monkeypatch, response=_block(kind="consultation", summary="poradil som", stage="done"))

        await orchestrator.run_consult_turn(db_session, version.id)

        assert prompts and _DEDO_TEXT not in prompts[0]
        db_session.refresh(msg)
        assert msg.status == "pending"

    async def test_several_messages_all_arrive_in_order(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id)
        dedo_message.record_dedo_message(db_session, version_id=version.id, content="Najprv aktualizuj nex-shared.")
        dedo_message.record_dedo_message(db_session, version_id=version.id, content="Potom spusti build znova.")
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, version.id)

        assert prompts[0].index("Najprv aktualizuj nex-shared.") < prompts[0].index("Potom spusti build znova.")


# ── 4. which build Dedo is answering ──────────────────────────────────────────


class TestVersionAwaitingDedo:
    def test_finds_the_build_blocked_on_framework_issue(self, db_session):
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")
        assert dedo_message.version_awaiting_dedo(db_session, project.slug).id == version.id

    def test_refuses_when_nothing_is_waiting_on_dedo(self, db_session):
        """A typo'd slug looks exactly like a healthy project — writing into it anyway is worse than refusing."""
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="agent_working")
        with pytest.raises(dedo_message.DedoMessageError):
            dedo_message.version_awaiting_dedo(db_session, project.slug)

    def test_refuses_an_unknown_project(self, db_session):
        with pytest.raises(dedo_message.DedoMessageError):
            dedo_message.version_awaiting_dedo(db_session, "no-such-project-here")

    def test_refuses_when_two_builds_wait(self, db_session):
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")
        second = Version(project_id=version.project_id, version_number="2.0.0")
        db_session.add(second)
        db_session.flush()
        _seed_state(db_session, second.id, status="blocked", block_reason="framework_issue")
        with pytest.raises(dedo_message.DedoMessageError):
            dedo_message.version_awaiting_dedo(db_session, project.slug)


# ── 5. the trigger — absent while blocked, armed by Dedo's unblock ────────────


class TestTheTriggerThatNowExists:
    """ICCINT-12 delivered nothing on its own; ICCINT-13 is what carries the message. Pinned, not described.

    This class used to be ``TestTheMissingTrigger`` and asserted the DEAD END: a build blocked on
    ``framework_issue`` offered ``{"nahlasit_znova"}``, which dispatches no turn, so Dedo's message stayed
    ``pending`` forever. The first half of that is still true and still pinned — while the build is BLOCKED
    nothing runs, and in particular the Manažér has no way to release it himself. What changed is the way
    out: Dedo's host-side unblock arms exactly one action, and that action carries the message.
    (The end-to-end path lives in ``tests/test_dedo_unblock.py``; this stays with the delivery tests because
    it is the release condition of THIS module.)

    "No way to release it himself" is asserted as EXECUTABILITY. The earlier version of this class checked
    only the OFFER (``determine_available_actions``) while saying the stronger thing in its docstring — and
    the stronger thing was not true: ``ask`` / ``answer`` / ``uprav`` all ran and cleared the block (audit,
    2026-08-22). A release condition that reads as satisfied while the hole is open is worse than one that
    stays silent, so it now tests what it claims.
    """

    def test_a_blocked_build_still_offers_no_action_that_runs_the_agent(self, db_session):
        version, _ = _make_version(db_session)
        state = _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")

        actions = orchestrator.determine_available_actions(state)

        assert actions == {"nahlasit_znova"}
        # ``nahlasit_znova`` re-sends the escalation TO Dedo; none of the verbs that arm a turn are offered.
        assert not actions & {"pokracovat", "uprav", "answer", "ask", "schvalit"}

    @pytest.mark.parametrize(
        "action,payload",
        [
            ("pokracovat", {}),
            ("uprav", {"comment": "sprav to inak"}),
            ("ask", {"text": "Čo sa deje?"}),
            ("answer", {"text": "Skús znova."}),
        ],
    )
    async def test_and_none_of_them_runs_it_when_called_anyway(self, db_session, action, payload):
        """Not offered AND not executable — the difference between a UI that hides a door and an engine that
        locks it. Each of these ends in ``_begin_dispatch``; each would have carried Dedo's message into a
        turn the Manažér started, in a NEX Studio that may not be fixed at all."""
        version, _ = _make_version(db_session)
        state = _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)

        with pytest.raises(orchestrator.OrchestratorError, match="technický tím"):
            await orchestrator.apply_action(db_session, version_id=version.id, action=action, payload=payload)

        assert (state.status, state.block_reason) == ("blocked", "framework_issue")
        db_session.refresh(msg)
        assert msg.status == "pending"  # nothing ran; nobody carried it

    async def test_nor_does_typing_into_the_chat(self, db_session):
        """The relay is the path with no button at all: it maps a typed message onto ``ask``/``answer``."""
        version, _ = _make_version(db_session)
        state = _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")

        with pytest.raises(orchestrator.OrchestratorError, match="technický tím"):
            await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="Pokračuj")

        assert (state.status, state.block_reason) == ("blocked", "framework_issue")

    def test_dedos_unblock_arms_exactly_one_action_that_does(self, db_session):
        """The release condition, the other way round: after the unblock there IS a trigger, it is a single
        button, and it is the resume verb (so the next turn — and Dedo's pending message with it) can run."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")
        dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)

        state = dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason="Opravené.")

        assert orchestrator.determine_available_actions(state) == {"pokracovat"}

    async def test_nahlasit_znova_does_not_arm_a_turn_so_the_message_stays_pending(self, db_session, monkeypatch):
        """The one available button, exercised for real: the state stays ``blocked`` (never
        ``agent_working``), so no dispatch is scheduled and Dedo's answer is not consumed."""
        version, _ = _make_version(db_session)
        _seed_state(
            db_session,
            version.id,
            current_stage="programovanie",
            status="blocked",
            block_reason="framework_issue",
        )
        msg = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_DEDO_TEXT)

        async def _no_escalation(**_kw):
            return None

        monkeypatch.setattr(orchestrator.dedo_escalation, "escalate_to_dedo", _no_escalation)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="nahlasit_znova", payload={})

        assert state.status == "blocked" and state.block_reason == "framework_issue"
        db_session.refresh(msg)
        assert msg.status == "pending"  # nothing ran; nobody carried it


# ── 6. the command Dedo actually types ────────────────────────────────────────


class TestTheHostCli:
    """``python -m backend.cli.dedo_message`` is — until ICCINT-14 — the ONLY way back to the agent, so it
    is exercised end-to-end here: argument resolution, stdin, the PERSISTED write, and every refusal.

    The write is asserted through the CLI's OWN session, which it opens and closes itself. The session is
    bound to the test connection in ``create_savepoint`` mode, so a CLI that forgot ``db.commit()`` would
    have its savepoint rolled back on ``close()`` and the row would vanish — the exact silent-drop
    regression (operator sees "zapísaná", agent gets nothing) this class exists to catch.
    """

    @pytest.fixture()
    def run_cli(self, db_connection, db_session, monkeypatch):
        def _factory():
            # ``expire_on_commit=False`` mirrors the production ``SessionLocal`` (backend/db/session.py)
            # exactly — a test session with different post-commit semantics would exercise a CLI that does
            # not exist in production.
            return Session(bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

        monkeypatch.setattr(cli_dedo, "SessionLocal", _factory)

        def _run(argv: list[str]) -> int:
            code = cli_dedo.main(argv)
            db_session.expire_all()  # re-read what the CLI's own session left behind
            return code

        return _run

    def test_writes_the_message_and_commits_it(self, db_session, run_cli, capsys):
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")

        code = run_cli(["--project", project.slug, "--message", _DEDO_TEXT])

        assert code == 0
        rows = _msgs(db_session, version.id)
        assert [(m.author, m.recipient, m.status, m.content) for m in rows] == [
            ("dedo", "ai_agent", "pending", _DEDO_TEXT)
        ]
        out = capsys.readouterr().out
        assert str(version.id) in out

    def test_version_id_names_a_build_directly(self, db_session, run_cli):
        """``--version-id`` bypasses the framework_issue resolution — a build that is not blocked can still
        be written to when the operator names it explicitly."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="agent_working")

        assert run_cli(["--version-id", str(version.id), "--message", _DEDO_TEXT]) == 0
        assert [m.content for m in _msgs(db_session, version.id)] == [_DEDO_TEXT]

    def test_reads_the_message_from_stdin(self, db_session, run_cli, monkeypatch):
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{_DEDO_TEXT}\n"))

        assert run_cli(["--project", project.slug]) == 0
        assert [m.content for m in _msgs(db_session, version.id)] == [_DEDO_TEXT]

    def test_refuses_an_empty_message(self, db_session, run_cli, capsys):
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="blocked", block_reason="framework_issue")

        assert run_cli(["--project", project.slug, "--message", "   \n "]) == 2
        assert _msgs(db_session, version.id) == []
        assert capsys.readouterr().err.strip()  # the operator is told why, not left guessing

    def test_refuses_an_unknown_project(self, db_session, run_cli):
        assert run_cli(["--project", "no-such-project-here", "--message", _DEDO_TEXT]) == 2

    def test_refuses_a_healthy_project_and_writes_nothing(self, db_session, run_cli):
        """A typo'd slug resolves to a project with no framework_issue build — refuse rather than file the
        answer into a build that never asked."""
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, status="agent_working")

        assert run_cli(["--project", project.slug, "--message", _DEDO_TEXT]) == 2
        assert _msgs(db_session, version.id) == []

    def test_refuses_a_malformed_version_id(self, db_session, run_cli):
        assert run_cli(["--version-id", "not-a-uuid", "--message", _DEDO_TEXT]) == 2

    def test_requires_a_target(self):
        """Neither ``--project`` nor ``--version-id`` — argparse must refuse rather than pick a build."""
        with pytest.raises(SystemExit) as exc:
            cli_dedo.main(["--message", _DEDO_TEXT])
        assert exc.value.code == 2
