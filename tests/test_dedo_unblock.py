"""ICCINT-13 — a build stuck on a NEX Studio bug can be released, and it actually moves again.

ICCINT-12 gave Dedo a voice; the build still could not go anywhere. ``blocked`` /
``block_reason='framework_issue'`` offered the Manažér one action (``nahlasit_znova``) that re-sends the
report and dispatches nothing, so a fixed NEX Studio changed nothing on the screen and Dedo's answer sat
``pending`` behind a turn that would never run.

These tests assert the BEHAVIOUR of the release, not its shape:

1. **The release itself** — it moves the state (``blocked``→``awaiting_manazer``, reason cleared, resume
   armed) and writes WHY, as Dedo, into the permanent thread.
2. **What it refuses** — an empty reason (the record is the point), and any build that is not stuck on a NEX
   Studio bug (there is no such thing as unblocking a healthy build, and a typo'd slug looks exactly like
   one).
3. **The Manažér cannot do it** — not merely "is not offered it": no verb exists, the board offers none,
   and EVERY verb that would start a turn (``pokracovat``, ``uprav``, ``ask``, ``answer``, and a typed chat
   message through the relay) is REFUSED while the build is blocked. This is a decision, not an omission:
   he cannot judge whether NEX Studio was really fixed, and a resume into the same broken version burns a
   round and re-blocks. The first cut of this file asserted the offer only, and three executable side doors
   survived underneath it (audit, 2026-08-22) — hence the executability tests.
4. **One button, any phase** — a NEX Studio bug can strike in any of the AI Agent's phases, so the released
   build offers exactly ONE action wherever it stopped, on the board the Manažér ACTUALLY sees (the HTTP
   route, not just the state-only helper — the route appends verbs of its own), and that press really
   reaches the agent.
5. **THE WHOLE PATH** — agent escalates → Dedo answers → Dedo releases → Manažér presses → the turn the
   press starts CARRIES Dedo's answer. Driven end-to-end through the real engine with only the headless
   ``claude`` CLI faked. If this one fails, ICCINT-12 and ICCINT-13 together buy nothing.

Where a press is exercised it is ROUTED the way production routes it (``pipeline_runner.run_one_turn``,
the same function the background runner calls) and asserted on what came back from the fake ``claude`` —
not on ``status == 'agent_working'``, which is only what ``apply_action`` just wrote. That distinction is
the whole reason the Vizuál dead end shipped: the state flipped, the flag was spent, and no agent was ever
called.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes.pipeline import router as pipeline_router
from backend.cli import dedo_unblock as cli_unblock
from backend.core.security import require_shu_or_above
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import dedo_message, dedo_unblock, orchestrator, pipeline_runner
from backend.services.dedo_message import DedoMessageError

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

_ANSWER = "Opravené v NEX Studiu v4.0.58 — port sa už nezamyká, skús ten build znova."
_REASON = "Zamykanie portu opravené vo v4.0.58; build môže pokračovať."
# Every phase a build can ACTUALLY be sitting in when NEX Studio breaks under it — i.e. every phase from
# which the engine settles ``blocked`` / ``framework_issue``: the generic Príprava dispatch, the Príprava
# conversation turn, the Vizuál round and the build round. All three are the AI Agent's.
#
# ``navrh`` and ``verifikacia`` are deliberately ABSENT, and that is an assertion, not an omission — the
# Návrh round has no framework escalation, and Verifikácia is the independent Auditor's, whose turn does not
# carry a message addressed to the AI Agent. Parametrizing over them (as the first cut did) tested states
# the engine cannot produce and hid the fact that resuming them would deliver nothing; ``unblock`` now
# refuses both loudly, pinned by ``test_a_phase_with_no_ai_agent_turn_is_refused...``.
_PHASES = ("priprava", "vizual", "programovanie")


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_user(db_session) -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_version(db_session, *, slug: str | None = None, user: User | None = None):
    user = user or _make_user(db_session)
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


def _seed_blocked(db_session, version_id, **overrides) -> PipelineState:
    """A build settled exactly the way ``_settle_framework_issue`` leaves it.

    ``current_actor`` is DERIVED from the phase (``STAGE_ACTOR``), never fixed at ``ai_agent``: a seed that
    pairs ``verifikacia`` with the AI Agent is a state the engine never produces, and a test parametrized
    over it proves something about a build that cannot exist.
    """
    defaults = {
        "version_id": version_id,
        "flow_type": "new_version",
        "current_stage": "priprava",
        "status": "blocked",
        "block_reason": "framework_issue",
        "next_action": "Čaká sa na Deda.",
        "mode": "conversation",
    }
    defaults.update(overrides)
    defaults.setdefault("current_actor", orchestrator.STAGE_ACTOR.get(defaults["current_stage"]) or "ai_agent")
    state = PipelineState(**defaults)
    db_session.add(state)
    db_session.flush()
    return state


def _stub_sandbox(monkeypatch):
    """Keep the Vizuál round's dev-server sandbox out of the test run (no docker, no Vite)."""
    from backend.services import vizual_sandbox

    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: f"http://sandbox.local/{slug}")


def _stub_build_round(db_session, version, project, monkeypatch):
    """Make the Programovanie round runnable off a real DB, without git or a live ``claude``.

    A build that escalated at Programovanie ALWAYS has a materialized task plan — the escalation comes out
    of the per-task coding turn, which only runs once the plan exists — so the seed has to have one too.
    Without it the resumed round opens with the plan-generation passes, which bypass the turn chokepoint
    (they emit plan JSON, not a status block) and would make the test measure the wrong turn.
    """
    from backend.db.models.tasks import Epic, Feat, Task

    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Základ", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schéma", status="todo")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="Vytvor tabuľku", task_type="backend", status="todo"))
    db_session.flush()
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)


async def _press_like_production(db_session, version_id, monkeypatch):
    """Press "Pokračovať" and run the turn it arms exactly the way the cockpit + runner do; return the
    prompts the headless ``claude`` was actually handed.

    The two production steps, in order and with nothing invented in between: ``apply_action`` (what the
    route calls), then ``dispatch_directive`` → ``pipeline_runner.run_one_turn`` (what the route schedules
    and the runner then does). Calling a round runner directly instead — the first cut called
    ``run_conversation_turn``, which is right for Príprava and wrong for Vizuál and Programovanie — is how a
    press that reached no agent at all passed as green.
    """
    prompts = _fake_cli(monkeypatch)
    _stub_sandbox(monkeypatch)
    state = await orchestrator.apply_action(db_session, version_id=version_id, action="pokracovat", payload={})
    directive = orchestrator.dispatch_directive(db_session, version_id, "pokracovat", {}, state.current_stage)
    await pipeline_runner.run_one_turn(db_session, version_id, None, directive, None)
    return prompts


def _status_block(kind="answer", summary="pokračujem", stage="priprava", question=None) -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": "manazer"}
    if question is not None:
        body["question"] = question
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _fake_cli(monkeypatch, *, responses=None):
    """Fake the headless ``claude`` seam; capture every prompt, answer from a scripted queue."""
    prompts: list[str] = []
    queue = list(responses or [])

    async def _fake(*, prompt, **_kw):
        prompts.append(prompt)
        return queue.pop(0) if queue else _status_block()

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    return prompts


def _silence_escalation(monkeypatch):
    """Keep the escalation delivery (channel file + Telegram) out of the test run."""

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(orchestrator.dedo_escalation, "escalate_to_dedo", _noop)


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


# ── 1. the release ────────────────────────────────────────────────────────────


class TestTheRelease:
    def test_it_moves_the_state_and_records_who_let_it_go_on_and_why(self, db_session):
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage="programovanie")

        state = dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=f"  {_REASON}  ")

        assert state.status == "awaiting_manazer"
        assert state.block_reason is None
        assert state.resume_after_framework_fix is True
        # The board must SAY what happened and what to press — a non-expert cannot infer either.
        assert "Pokračovať" in state.next_action
        # The phase is untouched: the build resumes where it stopped, it does not restart somewhere else.
        assert state.current_stage == "programovanie"

        # The reason is in the permanent thread, as Dedo, trimmed, and marked as the unblock.
        rows = _msgs(db_session, version.id)
        assert len(rows) == 1
        assert (rows[0].author, rows[0].content) == ("dedo", _REASON)
        assert rows[0].payload["dedo_unblock"] is True
        # …and it is a normal pending Dedo message, so the resumed turn reads it like any other answer.
        assert (rows[0].recipient, rows[0].status) == ("ai_agent", "pending")

    def test_the_reason_is_mandatory_and_nothing_moves_without_it(self, db_session):
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id)

        with pytest.raises(DedoMessageError):
            dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason="  \n ")

        assert state.status == "blocked" and state.block_reason == "framework_issue"
        assert state.resume_after_framework_fix is False
        assert _msgs(db_session, version.id) == []


# ── 2. what it refuses ────────────────────────────────────────────────────────


class TestTheGuard:
    @pytest.mark.parametrize(
        "overrides",
        [
            # A healthy build mid-work / settled — nothing is stuck.
            {"status": "agent_working", "block_reason": None},
            {"status": "awaiting_manazer", "block_reason": None},
            # Blocked, but for a reason that is the Manažér's to clear, not Dedo's.
            {"status": "blocked", "block_reason": "agent_question"},
            {"status": "blocked", "block_reason": "agent_error"},
            {"status": "blocked", "block_reason": "decision_needed"},
            # A cooperatively paused build.
            {"status": "paused", "block_reason": None},
        ],
    )
    def test_only_a_build_stuck_on_a_nex_studio_bug_can_be_released(self, db_session, overrides):
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, **overrides)

        with pytest.raises(DedoMessageError) as exc:
            dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        # The refusal SAYS what it found, so a mistyped slug is diagnosable from the message alone.
        assert overrides["status"] in str(exc.value)
        assert state.resume_after_framework_fix is False
        assert _msgs(db_session, version.id) == []

    def test_a_version_with_no_build_is_refused(self, db_session):
        version, _ = _make_version(db_session)
        with pytest.raises(DedoMessageError):
            dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

    @pytest.mark.parametrize("stage", ["done", "verifikacia"])
    def test_a_phase_with_no_ai_agent_turn_is_refused_rather_than_made_a_second_dead_end(self, db_session, stage):
        """The button must lead to a turn that CARRIES Dedo's answer, and only an AI-Agent turn does.

        ``done`` has no actor at all — ``_begin_dispatch`` no-ops and the Manažér presses a button that does
        nothing. ``verifikacia`` is worse because it looks fine: the independent Auditor's turn runs, but
        ``invoke_agent_with_parse_retry`` folds pending Dedo messages only for the AI Agent, so the answer
        stays ``pending`` and the release window is spent. Neither is reachable today (framework_issue is
        settled only from Príprava / Vizuál / Programovanie); refusing keeps it that way loudly.
        """
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage=stage)

        with pytest.raises(DedoMessageError, match="no AI Agent turn"):
            dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        assert state.status == "blocked" and state.resume_after_framework_fix is False
        assert _msgs(db_session, version.id) == []  # not even the reason — nothing was written

    def test_no_phase_a_build_can_block_in_is_refused(self):
        """The other direction: a guard that refuses more than reality would STRAND a genuinely stuck build
        with no way out at all. Every phase the engine can escalate from is an AI-Agent phase, so every one
        of them passes the guard — while ``verifikacia`` (the Auditor's) does not."""
        resumable = {s for s, a in orchestrator.STAGE_ACTOR.items() if a == orchestrator.AI_AGENT_ROLE}
        assert set(_PHASES) <= resumable
        assert "verifikacia" not in resumable and "done" not in resumable


# ── 3. the Manažér cannot do it ───────────────────────────────────────────────


class TestTheManazerCannotReleaseIt:
    """Not an omission — a decision. He cannot tell whether NEX Studio was really fixed.

    "Cannot" is asserted as EXECUTABILITY, not as absence from a menu. The first cut of ICCINT-13 guarded
    only ``pokracovat`` and tested only ``determine_available_actions``; ``uprav`` / ``ask`` / ``answer``
    and a typed chat message still ran, flipped the build to ``agent_working`` and cleared ``block_reason``
    on the way — the Manažér releasing the build himself, by the back door, with the record no longer saying
    so (audit, 2026-08-22, reproduced against the live engine).
    """

    def test_there_is_no_unblock_verb_at_all(self, db_session):
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id)

        assert "odblokovat" not in orchestrator._ACTIONS
        # Nothing in the whole verb set moves a framework_issue block: the only one that even mentions it
        # (``nahlasit_znova``) re-sends the report.
        assert orchestrator._ACTIONS & {"odblokovat", "unblock", "dedo_unblock"} == set()

    async def test_an_invented_verb_is_rejected(self, db_session):
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id)
        with pytest.raises(orchestrator.OrchestratorError, match="Unknown action"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="odblokovat", payload={})

    @pytest.mark.parametrize("stage", _PHASES)
    async def test_pokracovat_is_refused_while_the_build_is_still_blocked(self, db_session, stage):
        """Including at Programovanie, where the plain phase check would otherwise have let it through —
        that would be the Manažér releasing the build himself, by the back door."""
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage=stage)

        with pytest.raises(orchestrator.OrchestratorError, match="technický tím"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})

        assert state.status == "blocked" and state.block_reason == "framework_issue"

    def test_the_blocked_board_offers_him_only_the_re_report(self, db_session):
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage="programovanie")
        assert orchestrator.determine_available_actions(state) == {"nahlasit_znova"}

    @pytest.mark.parametrize(
        "action,payload",
        [
            ("uprav", {"comment": "sprav to inak"}),
            ("ask", {"text": "Čo sa deje?"}),
            ("answer", {"text": "Skús to znova."}),
            ("schvalit", {}),
            ("approve_spec", {}),
            ("overit_bez_opravy", {}),
        ],
    )
    async def test_no_other_verb_can_start_a_turn_either(self, db_session, action, payload):
        """The side doors, closed at the ENGINE. Each of these ends in ``_begin_dispatch``, and the status
        write clears ``block_reason`` — so "the cockpit does not show the button" was the only thing
        standing between the Manažér and a build resumed into the still-broken version."""
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage="programovanie")

        with pytest.raises(orchestrator.OrchestratorError, match="technický tím"):
            await orchestrator.apply_action(db_session, version_id=version.id, action=action, payload=payload)

        # Untouched: still blocked, still ON the framework issue, no turn armed.
        assert (state.status, state.block_reason) == ("blocked", "framework_issue")
        assert state.dispatch_in_flight is False

    async def test_a_typed_chat_message_does_not_release_it_either(self, db_session):
        """The relay is the path that does not need a button: it maps a typed message onto ``ask`` /
        ``answer`` and dispatches. It is refused with the SAME sentence, and nothing is written — the
        message is not quietly logged as if it had been delivered."""
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id)

        with pytest.raises(orchestrator.OrchestratorError, match="technický tím"):
            await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="Pokračuj prosím")

        assert (state.status, state.block_reason) == ("blocked", "framework_issue")
        assert _msgs(db_session, version.id) == []

    async def test_the_one_move_he_does_have_still_works(self, db_session, monkeypatch):
        """The gate refuses every verb but one — and that one must still go through, or the blocked screen
        is the locked dead end the ``nahlasit_znova`` button was added to remove."""
        version, _ = _make_version(db_session)
        _silence_escalation(monkeypatch)
        state = _seed_blocked(db_session, version.id)

        await orchestrator.apply_action(db_session, version_id=version.id, action="nahlasit_znova", payload={})

        assert (state.status, state.block_reason) == ("blocked", "framework_issue")
        assert any(m.author == "system" for m in _msgs(db_session, version.id))


# ── 4. one button, in any phase ───────────────────────────────────────────────


class TestTheOneButton:
    @pytest.mark.parametrize("stage", _PHASES)
    def test_a_released_build_offers_exactly_one_action_whatever_the_phase(self, db_session, stage):
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage=stage)

        state = dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        # ONE button. Not "pokracovat among the phase's usual menu" — at Príprava that menu offers
        # "Schváliť špecifikáciu" and four build verbs, none of which means "carry on where you stopped".
        assert orchestrator.determine_available_actions(state) == {"pokracovat"}

    @pytest.mark.parametrize("stage", _PHASES)
    async def test_that_press_actually_reaches_the_agent_in_that_phase(self, db_session, monkeypatch, stage):
        """The press must REACH THE AGENT — not merely leave ``status='agent_working'`` behind.

        Those are different claims, and the difference is exactly where ICCINT-13 broke: at Vizuál the state
        flipped, ``dispatch_in_flight`` went True, the resume flag was spent — and ``_run_vizual_round``
        then took its FRESH-ENTRY branch (``directive is None`` → re-show the preview, settle, call nobody).
        The button was gone, Dedo's answer was still ``pending``, and the build could not be released again
        because it was no longer blocked. Asserting on the three fields ``apply_action`` had just written
        could never see it; asserting on the prompt the fake ``claude`` received does.
        """
        version, project = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage=stage)
        if stage == "programovanie":
            _stub_build_round(db_session, version, project, monkeypatch)
        answer = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_ANSWER)
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        prompts = await _press_like_production(db_session, version.id, monkeypatch)

        assert prompts, f"{stage}: the press armed a turn that NEVER reached the agent"
        # Joined, not ``prompts[0]``: Programovanie legitimately opens with the plan pass and folds Dedo's
        # block into the coding brief that follows. The claim is that the answer REACHED the agent in that
        # resumed turn, not which of the turn's prompts carried it.
        carried = "\n".join(prompts)
        assert _ANSWER in carried  # Dedo's answer rode in with it …
        assert _REASON in carried  # … and so did WHAT was fixed
        assert "Dedo" in carried  # attributed — a Manažér cannot fix NEX Studio
        db_session.refresh(answer)
        assert answer.status == "delivered"
        # Resumed where it STOPPED, not restarted elsewhere. Asserted on the resume record rather than on
        # ``current_stage`` after the fact: a Programovanie turn that runs the plan out legitimately moves
        # the stage on afterwards (the conversation completion tail), and that is the build carrying on —
        # exactly what was wanted.
        assert [m.stage for m in _msgs(db_session, version.id) if (m.payload or {}).get("framework_fix_resume")] == [
            stage
        ]

    @pytest.mark.parametrize("stage", _PHASES)
    async def test_the_resumed_turn_tells_the_agent_why_it_is_running(self, db_session, stage):
        """A resume is a change-request, not a fresh phase entry. The directive is what says so — and at
        Vizuál it is also what decides whether the agent is called at all."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage=stage)
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})
        directive = orchestrator.dispatch_directive(db_session, version.id, "pokracovat", {}, state.current_stage)

        assert directive == orchestrator.FRAMEWORK_RESUME_DIRECTIVE

    async def test_the_vizual_round_never_swallows_a_turn_that_owes_an_answer(self, db_session, monkeypatch):
        """The backstop under the framing, tested where the swallow would happen.

        ``_run_vizual_round`` treats ``directive is None`` as "fresh entry — show the preview, call nobody",
        which is right for ``spustit_vizual`` and catastrophic for anything that armed a turn to CARRY
        something. A pending Dedo message is that "something", durably: it means the technical team answered
        this build's escalation. Driven straight at the round (not through the press) so it fails if the
        route-level framing is ever what is keeping the ``pokracovat`` path alive.
        """
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage="vizual")
        dedo_message.record_dedo_message(db_session, version_id=version.id, content=_ANSWER)
        state.status = "agent_working"
        db_session.flush()
        prompts = _fake_cli(monkeypatch)
        _stub_sandbox(monkeypatch)

        await orchestrator._run_vizual_round(db_session, state, directive=None)

        assert prompts, "a turn was armed with a Dedo answer pending and the round called nobody"
        assert _ANSWER in prompts[0]

    async def test_a_fresh_vizual_entry_with_nothing_pending_still_just_shows_the_preview(
        self, db_session, monkeypatch
    ):
        """The control for the backstop: ``spustit_vizual`` on a build with no Dedo message owed must NOT
        dispatch — the fresh entry exists to hand the Manažér a preview to walk."""
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage="vizual", status="agent_working")
        state.block_reason = None
        db_session.flush()
        prompts = _fake_cli(monkeypatch)
        _stub_sandbox(monkeypatch)

        await orchestrator._run_vizual_round(db_session, state, directive=None)

        assert prompts == []
        assert state.status == "awaiting_manazer"

    async def test_an_ordinary_pause_resume_is_still_a_fresh_dispatch(self, db_session):
        """The framing must attach to the framework resume ONLY: a Manažér who merely un-pauses the coding
        loop has said nothing to the agent, and inventing a "NEX Studio was fixed" line for him would be a
        lie in the prompt."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage="programovanie", status="paused", block_reason=None)

        await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})

        assert orchestrator.dispatch_directive(db_session, version.id, "pokracovat", {}, "programovanie") is None

    @pytest.mark.parametrize("stage", _PHASES)
    async def test_the_button_is_spent_by_one_press(self, db_session, stage):
        """The flag is a WAITING marker, not history: once a turn is running, the resume affordance is gone,
        so a settled turn later cannot re-offer "press Pokračovať" for a build that already went on."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage=stage)
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})

        assert state.resume_after_framework_fix is False

    async def test_a_relayed_chat_message_also_spends_it(self, db_session):
        """The Manažér can still WRITE instead of pressing (the composer is not an action). That path starts
        a turn too, so it must consume the marker — otherwise the button would come back after that turn
        settled, telling him to restart a build that had already carried on."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id)
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        state = await orchestrator.apply_action(
            db_session, version_id=version.id, action="ask", payload={"text": "Čo to bolo?"}
        )

        assert state.status == "agent_working"
        assert state.resume_after_framework_fix is False

    async def test_the_ordinary_resume_is_untouched_outside_programovanie(self, db_session):
        """Extending ``pokracovat`` must not turn it into a universal resume: without the marker it is still
        the Programovanie-only pause boundary."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id, current_stage="navrh", status="paused", block_reason=None)

        with pytest.raises(orchestrator.OrchestratorError, match="Programovanie"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})


# ── 4b. the board the Manažér actually sees ───────────────────────────────────


class TestTheBoardThroughTheRoute:
    """The "one button" promise belongs to the BOARD, and the board is not
    ``determine_available_actions``.

    ``_board()`` calls that helper and then APPENDS verbs of its own from DB-derived signals. Testing the
    helper alone therefore tests the promise on the one layer where it cannot fail: ``overit_znovu`` attaches
    to any settled version whose verified PASS has drifted, and a released build is settled — so a build
    stopped on a NEX Studio bug offered ``['overit_znovu', 'pokracovat']`` through the real route while every
    service-level test read ``{"pokracovat"}`` (audit, 2026-08-22). These drive the HTTP route.
    """

    @pytest.fixture()
    def client(self, db_session, monkeypatch):
        async def _fake_claude(**_kw):
            return ""

        monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)
        scheduled: list = []
        monkeypatch.setattr(
            pipeline_runner, "schedule_dispatch", lambda vid, directive=None: scheduled.append((vid, directive))
        )

        app = FastAPI()
        app.include_router(pipeline_router, prefix="/api/v1/pipeline")
        user = _make_user(db_session)

        def _override_db():
            yield db_session

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_shu_or_above] = lambda: user
        with TestClient(app) as c:
            c._scheduled = scheduled
            c._user = user
            yield c
        app.dependency_overrides.clear()

    @pytest.mark.parametrize("stage", _PHASES)
    def test_a_released_build_shows_one_button_even_when_the_route_has_more_to_offer(
        self, db_session, client, monkeypatch, stage
    ):
        """A DRIFTED verified version is the realistic case: it is the state that makes the route append
        ``overit_znovu``, and drift is orthogonal to a framework block — a build can perfectly well have
        both. The clamp is what keeps the promise on the screen."""
        version, _ = _make_version(db_session, user=client._user)
        _seed_blocked(db_session, version.id, current_stage=stage)
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)
        monkeypatch.setattr(orchestrator, "version_verified", lambda db, vid: (False, "sha_drift"))

        body = client.get(f"/api/v1/pipeline/{version.id}").json()

        assert body["available_actions"] == ["pokracovat"], body["available_actions"]
        # …and the flag the FE bar keys on survives serialization (the bar explains WHAT was repaired).
        assert body["state"]["resume_after_framework_fix"] is True

    def test_a_blocked_build_shows_only_the_re_report_even_when_drifted(self, db_session, client, monkeypatch):
        version, _ = _make_version(db_session, user=client._user)
        _seed_blocked(db_session, version.id, current_stage="programovanie")
        monkeypatch.setattr(orchestrator, "version_verified", lambda db, vid: (False, "sha_drift"))

        body = client.get(f"/api/v1/pipeline/{version.id}").json()

        assert body["available_actions"] == ["nahlasit_znova"], body["available_actions"]

    def test_the_press_schedules_a_dispatch_that_carries_the_framing(self, db_session, client):
        """End of the route's own responsibility: POST the one offered action and the background dispatch is
        scheduled WITH the framework-fix directive — the thing that makes Vizuál dispatch instead of settle."""
        version, _ = _make_version(db_session, user=client._user)
        _seed_blocked(db_session, version.id, current_stage="vizual")
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "pokracovat"})

        assert r.status_code == 200, r.text
        assert client._scheduled == [(version.id, orchestrator.FRAMEWORK_RESUME_DIRECTIVE)]

    @pytest.mark.parametrize(
        "action,payload",
        [("pokracovat", {}), ("uprav", {"comment": "inak"}), ("ask", {"text": "?"}), ("answer", {"text": "!"})],
    )
    def test_the_side_doors_are_refused_over_http_too(self, db_session, client, action, payload):
        """Not only in the service: a caller with a token and curl gets the same answer as the cockpit."""
        version, _ = _make_version(db_session, user=client._user)
        _seed_blocked(db_session, version.id, current_stage="programovanie")

        r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": action, "payload": payload})

        assert r.status_code == 400, r.text
        assert "technický tím" in r.json()["detail"]
        assert client._scheduled == []

    def test_the_relay_endpoint_is_refused_too(self, db_session, client):
        version, _ = _make_version(db_session, user=client._user)
        _seed_blocked(db_session, version.id, current_stage="programovanie")

        r = client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pokračuj"})

        assert r.status_code == 400, r.text
        assert "technický tím" in r.json()["detail"]


# ── 5. the whole path ─────────────────────────────────────────────────────────


class TestTheWholePath:
    """agent escalates → Dedo answers → Dedo releases → Manažér presses → the turn carries the answer.

    Tested as ONE path on purpose. Each half passing in isolation proves nothing: the message ICCINT-12
    writes is only worth writing if some turn eventually carries it, and the turn ICCINT-13 arms is only
    worth arming if it carries the message. Only the headless ``claude`` CLI is faked.
    """

    async def test_the_agents_escalation_comes_back_carrying_dedos_answer(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        state = PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
            next_action="rozhovor",
            mode="conversation",
        )
        db_session.add(state)
        db_session.flush()
        _silence_escalation(monkeypatch)

        # (1) The agent hits a bug it cannot fix — it is in NEX Studio itself — and escalates.
        prompts = _fake_cli(
            monkeypatch,
            responses=[
                _status_block(
                    kind="framework_issue",
                    summary="NEX Studio zamyká port",
                    question="Sandbox nevie naštartovať: NEX Studio drží port 5173.",
                )
            ],
        )
        await orchestrator.run_conversation_turn(db_session, version.id)
        assert state.status == "blocked" and state.block_reason == "framework_issue"
        # The dead end this task removes: the Manažér's only move re-sends the report.
        assert orchestrator.determine_available_actions(state) == {"nahlasit_znova"}

        # (2) Dedo answers the agent (ICCINT-12) …
        answer = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_ANSWER)
        assert answer.status == "pending"

        # (3) … and, the fix having landed, releases the build (ICCINT-13).
        dedo_unblock.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)
        assert state.status == "awaiting_manazer"
        assert orchestrator.determine_available_actions(state) == {"pokracovat"}

        # (4) The Manažér presses the one button …
        await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})
        assert state.status == "agent_working"

        # (5) … and the turn it starts CARRIES Dedo's answer to the agent.
        await orchestrator.run_conversation_turn(db_session, version.id)
        resumed_prompt = prompts[-1]
        assert _ANSWER in resumed_prompt
        # The unblock reason rides along too — the agent learns WHAT was fixed, not merely "go on".
        assert _REASON in resumed_prompt
        # Named as Dedo: the agent must not read it as the Manažér's instruction (a Manažér cannot fix
        # NEX Studio — that is why the build escalated in the first place).
        assert "Dedo" in resumed_prompt

        db_session.refresh(answer)
        assert answer.status == "delivered"
        # And the build is moving again — settled back to the Manažér by its own turn, not stuck.
        assert state.status == "awaiting_manazer"
        assert state.resume_after_framework_fix is False

    async def test_without_the_release_the_answer_never_moves(self, db_session, monkeypatch):
        """The control for the test above: same setup, no unblock. Nothing carries the message — which is
        precisely why ICCINT-12 could not ship alone."""
        version, _ = _make_version(db_session)
        _seed_blocked(db_session, version.id)
        _silence_escalation(monkeypatch)
        answer = dedo_message.record_dedo_message(db_session, version_id=version.id, content=_ANSWER)

        await orchestrator.apply_action(db_session, version_id=version.id, action="nahlasit_znova", payload={})
        await orchestrator.run_conversation_turn(db_session, version.id)

        db_session.refresh(answer)
        assert answer.status == "pending"


# ── 6. the command Dedo actually types ────────────────────────────────────────


class TestTheHostCli:
    """``python -m backend.cli.dedo_unblock`` is the ONLY way to release a build (charter §4.5 keeps Dedo
    off the API until ICCINT-14). Exercised end-to-end through the CLI's OWN session — which it opens and
    commits itself — so a forgotten ``db.commit()`` would roll the savepoint back and be caught here.
    """

    @pytest.fixture
    def run_cli(self, db_connection, db_session, monkeypatch):
        def _factory():
            from sqlalchemy.orm import Session as _Session

            return _Session(bind=db_connection, join_transaction_mode="create_savepoint")

        monkeypatch.setattr(cli_unblock, "SessionLocal", _factory)

        def _run(argv: list[str]) -> int:
            code = cli_unblock.main(argv)
            db_session.expire_all()
            return code

        return _run

    def test_releases_the_project_build_and_commits_it(self, db_session, run_cli, capsys):
        version, project = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, current_stage="programovanie")
        db_session.commit()

        assert run_cli(["--project", project.slug, "--reason", _REASON]) == 0

        db_session.refresh(state)
        assert state.status == "awaiting_manazer"
        assert state.resume_after_framework_fix is True
        rows = _msgs(db_session, version.id)
        assert [(m.author, m.content) for m in rows] == [("dedo", _REASON)]
        # The operator's receipt says what the Manažér will now see — the command's whole point.
        assert "odblokovaná" in capsys.readouterr().out

    def test_version_id_names_a_build_directly(self, db_session, run_cli):
        version, _ = _make_version(db_session)
        state = _seed_blocked(db_session, version.id)
        db_session.commit()

        assert run_cli(["--version-id", str(version.id), "--reason", _REASON]) == 0

        db_session.refresh(state)
        assert state.status == "awaiting_manazer"

    def test_reads_the_reason_from_stdin(self, db_session, run_cli, monkeypatch):
        import io

        version, project = _make_version(db_session)
        state = _seed_blocked(db_session, version.id)
        db_session.commit()
        monkeypatch.setattr("sys.stdin", io.StringIO(_REASON))

        assert run_cli(["--project", project.slug]) == 0

        db_session.refresh(state)
        assert state.resume_after_framework_fix is True

    def test_refuses_an_empty_reason_and_writes_nothing(self, db_session, run_cli, capsys, monkeypatch):
        import io

        version, project = _make_version(db_session)
        state = _seed_blocked(db_session, version.id)
        db_session.commit()
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n "))

        assert run_cli(["--project", project.slug]) == 2

        db_session.refresh(state)
        assert state.status == "blocked"
        assert "dôvod" in capsys.readouterr().err

    def test_refuses_a_healthy_project_and_changes_nothing(self, db_session, run_cli):
        version, project = _make_version(db_session)
        state = _seed_blocked(db_session, version.id, status="awaiting_manazer", block_reason=None)
        db_session.commit()

        assert run_cli(["--project", project.slug, "--reason", _REASON]) == 2

        db_session.refresh(state)
        assert state.resume_after_framework_fix is False
        assert _msgs(db_session, version.id) == []

    def test_refuses_an_unknown_project(self, db_session, run_cli):
        assert run_cli(["--project", "nie-je-taky-projekt", "--reason", _REASON]) == 2

    def test_refuses_a_malformed_version_id(self, db_session, run_cli):
        assert run_cli(["--version-id", "nie-uuid", "--reason", _REASON]) == 2

    def test_requires_a_target(self):
        with pytest.raises(SystemExit):
            cli_unblock.main(["--reason", _REASON])
