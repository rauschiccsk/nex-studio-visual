"""ICCINT-24 — Dedo's findings reach the Manažér as a PROPOSAL, and reach the agent only by his click.

The return leg built in ICCINT-12/13/14 answered the wrong half of the problem. It carries Dedo's answer to
an agent that escalated a bug in NEX Studio itself (``framework_issue``) — a case that has not fired once in
the product's life. The case that fires constantly is the opposite: the Director asks Dedo to review the
agent's work, Dedo measures and finds mistakes, and those findings reached the agent only because the
Director read Dedo's text and RETYPED it into the cockpit. Four times in one day.

What may NOT change is who decides. A Dedo message is not a note in a thread — it is prepended to the
agent's next prompt as an instruction to follow (ICCINT-14 §4.5), so an unrestricted Dedo would quietly be
steering every customer's build. So the finding arrives as a proposal: recorded, shown to the Manažér,
delivered to nobody. He edits it if he wants and presses send — or declines it.

The tests are the guarantees, not the plumbing:

1. **A proposal never reaches the agent.** Not through ``pending_messages``, not through
   ``pending_for_prompt``, and — the test that actually matters — not through a REAL turn: the prompt the
   headless CLI is handed must be BYTE FOR BYTE the same as it would have been with no proposal at all.
   Stated as equality, not as "Dedo's text is absent": the audit of 2026-08-23 walked a truncated excerpt
   past the old blacklist with all 30 tests green. A control case in the same twin harness (a PENDING
   ICCINT-12 message, which IS a delivery) proves the comparison can see a difference when there is one.
2. **Dedo may PROPOSE into a healthy build, and still may not WRITE into one.** The ICCINT-14 boundary is
   untouched; the proposal is allowed everywhere precisely because it delivers nothing.
3. **What the Manažér saw is what happens.** Send and reject name the proposal that was on his screen, and
   refuse (409) if it is no longer open. Re-resolving "the currently open proposal" at click time meant the
   button said one thing and the engine did another whenever Dedo wrote in between — the same audit,
   finding 1.
4. **At most ONE proposal is open per build.** A new finding archives the previous one as ``superseded``,
   so handling the newest cannot uncover a stale one that looks new (finding 2).
5. **The click sends it with the proposal's own action, with that action's guards** — an ``answer`` on a
   build that is not asking anything is refused exactly as it always was, and the proposal stays open.
6. **What the Manažér edited is what goes on the record** — and Dedo's original stays findable.
7. **A declined proposal is never offered again and never delivered.**
8. (The cockpit half — the bar renders only when a proposal exists — is
   ``frontend/src/__tests__/components/test_DedoProposalBar.test.tsx``.)
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

import pytest
from sqlalchemy import select

from backend.config.settings import settings
from backend.core import dedo_auth
from backend.db.models.foundation import User, UserSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import auth as auth_service
from backend.services import dedo_message, orchestrator, pipeline_runner

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

#: An obviously fake stand-in for Dedo's machine token: 64 chars, one repeated character.
_TOKEN = "d" * 64

_FINDING = (
    "Prekontroloval som Plán úloh: úloha #4 nemá test na zápornú cenu, hoci špecifikácia ho žiada v §3.2. "
    "Doplň ho a spusti overenie znova."
)
_EDITED = _FINDING + " Prosím, začni tým, nech to vieme ešte dnes."


# ── fixtures / seeds ──────────────────────────────────────────────────────────


@pytest.fixture()
def dedo_token(monkeypatch) -> str:
    """Configure this instance with Dedo's machine identity (plaintext form — the door reads settings)."""
    monkeypatch.setattr(settings, "dedo_api_token_sha256", "")
    monkeypatch.setattr(settings, "dedo_api_token", _TOKEN)
    return _TOKEN


@pytest.fixture()
def no_dispatch(monkeypatch) -> list:
    """Capture the background dispatch instead of running an agent, and return the captured directives."""
    scheduled: list = []

    def _capture(version_id, directive=None):
        scheduled.append((version_id, directive))

    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", _capture)
    return scheduled


def _dedo_auth() -> dict[str, str]:
    return {dedo_auth.DEDO_TOKEN_HEADER: _TOKEN}


def _make_user(db_session, role: str = "ri") -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserSession(user_id=user.id, token_version=0))
    db_session.flush()
    return user


def _make_version(db_session, user: User, *, version_number: Optional[str] = None) -> Version:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,
    )
    db_session.add(project)
    db_session.flush()
    # ``version_number`` is pinned only by the prompt-equality test: the conversation brief names the
    # version's spec paths, so two builds must share a number for their prompts to be comparable at all.
    version = Version(project_id=project.id, version_number=version_number or f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def _seed_state(db_session, version_id, **overrides) -> PipelineState:
    """A HEALTHY build by default — the case this whole ticket is about."""
    defaults = {
        "version_id": version_id,
        "flow_type": "new_version",
        "current_stage": "priprava",
        "current_actor": "ai_agent",
        "status": "awaiting_manazer",
        "next_action": "Čaká na Manažéra.",
        "mode": "conversation",
    }
    defaults.update(overrides)
    state = PipelineState(**defaults)
    db_session.add(state)
    db_session.flush()
    return state


def _build(
    db_session, *, version_number: Optional[str] = None, **state_overrides
) -> tuple[User, Version, PipelineState]:
    user = _make_user(db_session)
    version = _make_version(db_session, user, version_number=version_number)
    state = _seed_state(db_session, version.id, **state_overrides)
    return user, version, state


#: The version number the twin builds of the prompt-equality test share (see
#: ``TestAProposalNeverReachesTheAgent.test_the_prompt_is_byte_for_byte_the_same_with_and_without_it``).
_TWIN_VERSION = "1.0.0"


def _bearer(user: User) -> dict[str, str]:
    token, _ = auth_service.create_access_token(user, 0, 60)
    return {"Authorization": f"Bearer {token}"}


def _propose(db_session, version_id, *, content: str = _FINDING, action: str = "uprav") -> PipelineMessage:
    """Dedo leaves a finding — COMMITTED, exactly as his door does it.

    The commit is load-bearing for the refusal tests: in production a proposal is a committed row from an
    earlier request, so a later send that the engine refuses rolls back only ITS OWN work and the finding
    survives. An uncommitted fixture row would be swallowed by that rollback and the test would be proving
    something the product never does.
    """
    msg = dedo_message.record_dedo_proposal(
        db_session,
        version_id=version_id,
        content=content,
        proposed_action=action,
    )
    db_session.commit()
    return msg


def _send(client, user: User, version_id, proposal, *, text: str = _FINDING):
    """The Manažér presses send on the proposal he is looking at.

    ``message_id`` is not decoration: the endpoint acts on the proposal NAMED here and refuses if that one
    is no longer open, instead of re-resolving "whatever is open now" at click time (audit 2026-08-23,
    finding 1). Every test goes through this helper so no call site can silently drop it.
    """
    return client.post(
        f"/api/v1/pipeline/{version_id}/dedo-proposal/send",
        headers=_bearer(user),
        json={"message_id": str(proposal.id if hasattr(proposal, "id") else proposal), "text": text},
    )


def _reject(client, user: User, version_id, proposal):
    """The Manažér declines the proposal he is looking at — named, for the same reason as :func:`_send`."""
    return client.post(
        f"/api/v1/pipeline/{version_id}/dedo-proposal/reject",
        headers=_bearer(user),
        json={"message_id": str(proposal.id if hasattr(proposal, "id") else proposal)},
    )


def _manazer_messages(db_session, version_id) -> list[PipelineMessage]:
    return list(
        db_session.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _block(kind="answer", summary="pokračujem", stage="priprava") -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": "manazer"}
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _fake_cli(monkeypatch) -> list[str]:
    """Fake the headless ``claude`` seam and capture every prompt the agent is actually handed."""
    prompts: list[str] = []

    async def _fake(*, prompt, **_kw):
        prompts.append(prompt)
        return _block()

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    return prompts


# ── 1. THE GUARANTEE: a proposal never reaches the agent ──────────────────────


class TestAProposalNeverReachesTheAgent:
    """The whole point of the ticket. A finding the Manažér has not clicked must not exist as far as the
    agent is concerned — not "should not normally", but cannot, by construction."""

    def test_it_is_recorded_as_dedos_but_addressed_to_the_manazer_and_not_pending(self, db_session):
        _user, version, _state = _build(db_session)

        msg = _propose(db_session, version.id, content=f"  {_FINDING}  ")

        assert msg.author == "dedo"
        assert msg.content == _FINDING  # trimmed
        # Addressed to the HUMAN. A delivery query that forgot the status filter would still not find it.
        assert msg.recipient == "manazer"
        # And ``pending`` is what "the next turn carries this" means — a proposal is deliberately not that.
        assert msg.status == "proposed"
        assert msg.payload["dedo_proposal"] is True
        assert msg.payload["proposed_action"] == "uprav"

    def test_the_delivery_queue_does_not_see_it(self, db_session):
        _user, version, _state = _build(db_session)
        _propose(db_session, version.id)

        assert dedo_message.pending_messages(db_session, version.id) == []
        assert dedo_message.pending_for_prompt(db_session, version.id) == (None, [])

    async def test_the_prompt_is_byte_for_byte_the_same_with_and_without_it(self, db_session, monkeypatch):
        """The one that matters: the actual prompt the headless CLI receives — asserted as EQUALITY.

        It used to be a blacklist (``_FINDING not in prompt``), and a blacklist tests the leak it imagines
        rather than the guarantee. The 2026-08-23 audit demonstrated it: a NEW escape route added to
        ``run_conversation_turn`` — read ``open_proposal()``, prepend ``content[:90]`` as "context for the
        agent" — left all 30 tests GREEN while the agent really was handed the finding, because the text
        arrived truncated instead of verbatim. Anything reworded, summarised or translated would have
        passed the same way, and "helpfully summarise what Dedo said" is precisely the convenience someone
        adds later.

        So the claim is stated as what it actually is: an OPEN PROPOSAL MUST NOT INFLUENCE THE PROMPT AT
        ALL. Two builds, identical in everything the brief reads (same version number → same spec paths),
        one carrying a proposal and one not; the prompts must be identical character for character. Any
        route that lets a ``proposed`` row affect the prompt — verbatim, shortened, rephrased, or reduced to
        a single hint that one exists — moves those bytes and fails here.
        """
        _u1, with_proposal, _s1 = _build(db_session, version_number=_TWIN_VERSION, status="agent_working")
        _u2, without, _s2 = _build(db_session, version_number=_TWIN_VERSION, status="agent_working")
        proposal = _propose(db_session, with_proposal.id)
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, with_proposal.id)
        await orchestrator.run_conversation_turn(db_session, without.id)

        assert len(prompts) == 2, "both turns must have reached the CLI — otherwise the comparison is vacuous"
        assert prompts[0] == prompts[1], (
            "an open proposal changed the prompt the agent is handed. Whatever the wording — the whole "
            "text, an excerpt, a summary, or just a note that a finding is waiting — the Manažér has not "
            "sent it, so the agent must not be able to tell it exists."
        )
        # …and the row is untouched: still waiting for the Manažér, not quietly marked delivered.
        db_session.refresh(proposal)
        assert proposal.status == "proposed"

    async def test_the_control_case_proves_the_harness_would_have_caught_it(self, db_session, monkeypatch):
        """The same twin harness with a PENDING (ICCINT-12) Dedo message — which DOES reach the agent.

        Without this, the equality above would also hold if the harness compared two prompts that nothing
        could ever change. Here the only difference between the twins is a message that IS a delivery, and
        the comparison sees it — so the negative is a measurement, not a tautology.
        """
        _u1, with_message, _s1 = _build(db_session, version_number=_TWIN_VERSION, status="agent_working")
        _u2, without, _s2 = _build(db_session, version_number=_TWIN_VERSION, status="agent_working")
        dedo_message.record_dedo_message(db_session, version_id=with_message.id, content=_FINDING)
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, with_message.id)
        await orchestrator.run_conversation_turn(db_session, without.id)

        assert len(prompts) == 2
        assert prompts[0] != prompts[1]
        assert _FINDING in prompts[0] and _FINDING not in prompts[1]

    def test_the_writer_refuses_a_verb_the_manazer_has_no_button_for(self, db_session):
        """A proposal is sent BY an existing Manažér action. An unknown verb has no button, so it would be
        a proposal that can never be sent — refused at the write, not discovered at the click."""
        _user, version, _state = _build(db_session)

        with pytest.raises(dedo_message.DedoMessageError):
            _propose(db_session, version.id, action="schvalit")
        with pytest.raises(dedo_message.DedoMessageError):
            _propose(db_session, version.id, action="")

    def test_an_empty_finding_is_refused(self, db_session):
        _user, version, _state = _build(db_session)
        with pytest.raises(dedo_message.DedoMessageError):
            _propose(db_session, version.id, content="   ")

    def test_a_version_with_no_build_is_refused(self, db_session):
        user = _make_user(db_session)
        version = _make_version(db_session, user)  # no pipeline_state
        with pytest.raises(dedo_message.DedoMessageError):
            _propose(db_session, version.id)


# ── 2. Dedo may PROPOSE anywhere; he still may not WRITE where he was not asked ────


class TestTheIccint14BoundaryStillHolds:
    def test_dedo_may_propose_into_a_healthy_build(self, client, db_session, dedo_token):
        """The whole reason the endpoint exists: the ordinary case is a build that never escalated."""
        _user, version, _state = _build(db_session)

        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/proposals",
            headers=_dedo_auth(),
            json={"content": _FINDING, "proposed_action": "uprav"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["content"] == _FINDING
        assert body["status"] == "proposed"
        assert body["author"] == "dedo" and body["recipient"] == "manazer"
        # Stamped as having come over the wire, like every other write on that door.
        assert body["payload"]["dedo_transport"] == "api"

    def test_dedo_still_may_not_WRITE_into_that_same_healthy_build(self, client, db_session, dedo_token):
        """ICCINT-14's line, unmoved. A direct message is the directive that opens the agent's next prompt;
        a build that never asked must not receive one — no matter that proposing into it is now allowed."""
        _user, version, _state = _build(db_session)

        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/messages",
            headers=_dedo_auth(),
            json={"content": _FINDING},
        )

        assert resp.status_code == 409, resp.text
        assert dedo_message.pending_messages(db_session, version.id) == []

    def test_the_proposal_door_is_shut_without_the_machine_token(self, client, db_session, dedo_token):
        _user, version, _state = _build(db_session)
        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/proposals",
            json={"content": _FINDING, "proposed_action": "uprav"},
        )
        assert resp.status_code == 401

    def test_an_unknown_action_is_refused_at_the_door(self, client, db_session, dedo_token):
        _user, version, _state = _build(db_session)
        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/proposals",
            headers=_dedo_auth(),
            json={"content": _FINDING, "proposed_action": "schvalit"},
        )
        assert resp.status_code == 409

    def test_a_version_that_was_never_built_is_a_404(self, client, db_session, dedo_token):
        user = _make_user(db_session)
        version = _make_version(db_session, user)
        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/proposals",
            headers=_dedo_auth(),
            json={"content": _FINDING, "proposed_action": "uprav"},
        )
        assert resp.status_code == 404


# ── 3. the board offers it — and only while it is open ────────────────────────


class TestTheBoardOffersItHonestly:
    def test_the_board_carries_the_open_proposal(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="ask")

        board = client.get(f"/api/v1/pipeline/{version.id}", headers=_bearer(user)).json()

        assert board["dedo_proposal"] is not None
        assert board["dedo_proposal"]["message_id"] == str(proposal.id)
        assert board["dedo_proposal"]["content"] == _FINDING
        assert board["dedo_proposal"]["proposed_action"] == "ask"

    def test_a_build_with_no_proposal_says_so(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        board = client.get(f"/api/v1/pipeline/{version.id}", headers=_bearer(user)).json()
        assert board["dedo_proposal"] is None

    def test_the_newest_finding_is_the_one_offered(self, client, db_session, no_dispatch):
        """Dedo re-measuring and proposing again is normal; the stale finding must not be what is offered."""
        user, version, _state = _build(db_session)
        _propose(db_session, version.id, content="Staršie zistenie.")
        newer = _propose(db_session, version.id, content=_FINDING)

        board = client.get(f"/api/v1/pipeline/{version.id}", headers=_bearer(user)).json()

        assert board["dedo_proposal"]["message_id"] == str(newer.id)

    def test_a_new_finding_closes_the_one_it_replaces(self, client, db_session, no_dispatch):
        """AT MOST ONE proposal is open per build — the older one is archived, not merely un-offered.

        It used to keep its ``proposed`` status "and simply stop being offered", which was not true of the
        system as a whole (audit 2026-08-23, finding 2): only the offered row was ever resolved, so the
        first send/reject uncovered the previous one and the bar came back carrying a stale finding that
        looked new. ``superseded`` is spelled differently from ``sent``/``rejected`` on purpose — the log
        must not claim the Manažér handled something he never saw.
        """
        _user, version, _state = _build(db_session)
        older = _propose(db_session, version.id, content="Staršie zistenie.")
        newer = _propose(db_session, version.id, content=_FINDING)

        db_session.refresh(older)
        assert older.status == "archived"
        assert older.payload["dedo_proposal_resolution"] == "superseded"
        assert older.content == "Staršie zistenie."  # the wording stays in the log
        assert dedo_message.open_proposal(db_session, version.id).id == newer.id

    def test_a_superseded_finding_does_NOT_come_back_after_the_newer_one_is_handled(
        self, client, db_session, no_dispatch
    ):
        """The failure this prevents, end to end: he clears his desk and the desk refills itself."""
        user, version, _state = _build(db_session)
        _propose(db_session, version.id, content="STARÝ NÁVRH — už neplatí")
        newer = _propose(db_session, version.id, content=_FINDING)

        resp = _reject(client, user, version.id, newer)

        assert resp.status_code == 200, resp.text
        assert resp.json()["dedo_proposal"] is None
        board = client.get(f"/api/v1/pipeline/{version.id}", headers=_bearer(user)).json()
        assert board["dedo_proposal"] is None, "a superseded finding came back as if it were new"


# ── 3b. what he decided about is what happens (audit 2026-08-23, finding 1) ────


class TestWhatHeSawIsWhatHappens:
    """The half of "the Manažér decides" that the first implementation dropped.

    Deciding means the thing you approved is the thing that runs. Both endpoints used to re-resolve "the
    open proposal" at click time, so a finding Dedo wrote between the Manažér reading his screen and
    pressing the button — a 25 s reconcile window, and re-measuring is Dedo's normal mode — was the one
    that got executed, with ITS verb. Pressing "Spýtať sa agenta" could run ``uprav`` instead: work handed
    back, failed tasks reset, the whole loop re-dispatched. And the finding he HAD read was archived as
    ``sent`` with the old text, having gone nowhere.
    """

    def test_the_verb_that_runs_is_the_verb_on_the_button_he_pressed(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        seen = _propose(db_session, version.id, content="OTÁZKA: ktorý formát faktúry?", action="ask")
        # …and while he reads it, Dedo measures again and proposes something else entirely.
        _propose(db_session, version.id, content="Vráť to a doplň testy.", action="uprav")

        resp = _send(client, user, version.id, seen, text="OTÁZKA: ktorý formát faktúry?")

        # His proposal is gone (superseded), so the honest answer is a refusal — never the other verb.
        assert resp.status_code == 409, resp.text
        assert "novší návrh" in resp.json()["detail"]
        assert _manazer_messages(db_session, version.id) == [], "an action ran that he never approved"
        assert no_dispatch == []
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "awaiting_manazer", "the build was set working by a verb he never pressed"

    def test_a_finding_he_never_saw_is_not_archived_as_sent(self, client, db_session, no_dispatch):
        """The second half of the same defect: the record must not say a message went out that did not.

        The newer finding used to be marked ``resolution='sent'`` with the OLDER text in ``sent_text`` —
        while nothing was delivered anywhere, and the conversation hid it (proposals are filtered from the
        transcript), so it vanished for everyone except someone reading SQL.
        """
        user, version, _state = _build(db_session)
        seen = _propose(db_session, version.id, content="OTÁZKA: ktorý formát faktúry?", action="ask")
        never_seen = _propose(db_session, version.id, content="Vráť to a doplň testy.", action="uprav")

        _send(client, user, version.id, seen, text="OTÁZKA: ktorý formát faktúry?")

        db_session.refresh(never_seen)
        assert never_seen.status == "proposed", "a finding nobody read was consumed by someone else's click"
        assert "dedo_proposal_resolution" not in (never_seen.payload or {})

    def test_the_proposal_that_gets_declined_is_the_one_he_declined(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        seen = _propose(db_session, version.id, content="Prvé zistenie.")
        newer = _propose(db_session, version.id, content="Druhé zistenie.")

        resp = _reject(client, user, version.id, seen)

        assert resp.status_code == 409, resp.text
        db_session.refresh(newer)
        assert newer.status == "proposed", "the finding he never read was the one that got declined"

    def test_the_ordinary_case_still_goes_through_untouched(self, client, db_session, no_dispatch):
        """Nothing changed for the normal path: he acts on what is on his screen and it runs."""
        user, version, _state = _build(db_session, status="blocked", block_reason="agent_question")
        proposal = _propose(db_session, version.id, action="answer")

        resp = _send(client, user, version.id, proposal)

        assert resp.status_code == 200, resp.text
        assert [m.kind for m in _manazer_messages(db_session, version.id)] == ["answer"]

    def test_a_proposal_from_another_build_cannot_be_decided_here(self, client, db_session, no_dispatch):
        """The named id is checked against the version in the path — an id is not a capability."""
        user, mine, _s1 = _build(db_session)
        _propose(db_session, mine.id)
        _other_user, theirs, _s2 = _build(db_session)
        elsewhere = _propose(db_session, theirs.id)

        resp = _send(client, user, mine.id, elsewhere)

        assert resp.status_code == 404, resp.text
        db_session.refresh(elsewhere)
        assert elsewhere.status == "proposed"

    def test_an_ordinary_message_id_is_not_a_proposal(self, client, db_session, no_dispatch):
        """Only a Dedo PROPOSAL can be sent this way — not any message id that happens to exist."""
        user, version, _state = _build(db_session)
        _propose(db_session, version.id)
        dedo_answer = dedo_message.record_dedo_message(db_session, version_id=version.id, content="odpoveď")
        db_session.commit()

        resp = _send(client, user, version.id, dedo_answer)

        assert resp.status_code == 404, resp.text


# ── 4. the click sends it with the proposal's OWN action, and its guards ──────


class TestTheClickSendsItAsTheManazer:
    def test_uprav_is_sent_as_the_manazers_own_return_message(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        resp = _send(client, user, version.id, proposal)

        assert resp.status_code == 200, resp.text
        sent = _manazer_messages(db_session, version.id)
        assert len(sent) == 1
        # The verb the proposal named — recorded exactly as if the Manažér had typed it into "Uprav".
        assert sent[0].kind == "return"
        assert sent[0].recipient == "ai_agent"
        assert sent[0].content == _FINDING
        # …and the turn really runs: the action left the agent working and a dispatch was scheduled.
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "agent_working"
        assert no_dispatch and no_dispatch[-1][0] == version.id

    def test_answer_is_sent_as_an_answer_when_the_agent_is_actually_asking(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session, status="blocked", block_reason="agent_question")
        proposal = _propose(db_session, version.id, action="answer")

        resp = _send(client, user, version.id, proposal)

        assert resp.status_code == 200, resp.text
        sent = _manazer_messages(db_session, version.id)
        assert [m.kind for m in sent] == ["answer"]
        # The directive the engine will hand the agent frames it as the MANAŽÉR's answer — because it is
        # his: he read it, kept it or changed it, and sent it.
        assert no_dispatch[-1][1] and "Manažér odpovedal" in no_dispatch[-1][1]

    def test_answer_is_REFUSED_when_the_agent_is_asking_nothing_and_the_proposal_survives(
        self, client, db_session, no_dispatch
    ):
        """The guard belongs to the action, and routing through the action is what keeps it.

        A proposal is not a licence: "answer" on a settled build is the same nonsense it always was, and it
        is refused the same way. The finding stays OPEN so the Manažér can send it when it applies — a
        refusal that silently consumed it would lose the technical team's work.
        """
        user, version, _state = _build(db_session, status="awaiting_manazer")
        proposal = _propose(db_session, version.id, action="answer")

        resp = _send(client, user, version.id, proposal)

        assert resp.status_code == 400, resp.text
        assert _manazer_messages(db_session, version.id) == []
        db_session.refresh(proposal)
        assert proposal.status == "proposed"
        assert no_dispatch == []

    def test_a_build_stopped_on_a_nex_studio_bug_refuses_the_send_too(self, client, db_session, no_dispatch):
        """ICCINT-13's framework-block gate is one of the guards inherited by routing through the action.

        Releasing such a build is Dedo's move, not the Manažér's — and a proposal must not become the side
        door that starts a turn into a version NEX Studio is still broken for.
        """
        user, version, _state = _build(db_session, status="blocked", block_reason="framework_issue")
        proposal = _propose(db_session, version.id, action="uprav")

        resp = _send(client, user, version.id, proposal)

        assert resp.status_code == 400, resp.text
        assert _manazer_messages(db_session, version.id) == []
        db_session.refresh(proposal)
        assert proposal.status == "proposed"

    def test_sending_a_proposal_this_build_never_had_is_a_404(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        resp = _send(client, user, version.id, uuid.uuid4())
        assert resp.status_code == 404

    def test_someone_elses_project_cannot_be_driven(self, client, db_session, no_dispatch):
        """The send is a Manažér action and carries the same ownership check every action does."""
        _owner, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id)
        stranger = _make_user(db_session, role="shu")

        resp = _send(client, stranger, version.id, proposal)

        assert resp.status_code == 403, resp.text


# ── 5. what he edited is what goes on the record; the original stays findable ──


class TestTheRecordSaysWhatWasActuallySent:
    def test_the_edited_text_is_what_reaches_the_agent_and_the_log(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        _send(client, user, version.id, proposal, text=_EDITED)

        sent = _manazer_messages(db_session, version.id)[0]
        assert sent.content == _EDITED
        # The engine's directive carries the SENT wording too — not the proposal's.
        assert no_dispatch[-1][1] and _EDITED in no_dispatch[-1][1]

    def test_dedos_original_wording_survives_the_edit(self, client, db_session, no_dispatch):
        """Both directions stay answerable months later: what did the technical team propose, and what
        actually went out."""
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        _send(client, user, version.id, proposal, text=_EDITED)

        db_session.refresh(proposal)
        # The proposal row keeps Dedo's words untouched…
        assert proposal.content == _FINDING
        assert proposal.payload["dedo_proposal_resolution"] == "sent"
        assert proposal.payload["sent_text"] == _EDITED
        assert proposal.payload["edited"] is True
        # …and the message that went out points back at it, carrying the original inline.
        sent = _manazer_messages(db_session, version.id)[0]
        assert proposal.payload["sent_message_id"] == str(sent.id)
        origin = sent.payload["dedo_proposal_origin"]
        assert origin["proposal_message_id"] == str(proposal.id)
        assert origin["original_content"] == _FINDING
        assert origin["proposed_action"] == "uprav"

    def test_an_unedited_send_says_so(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        _send(client, user, version.id, proposal)

        db_session.refresh(proposal)
        assert proposal.payload["edited"] is False

    def test_an_empty_box_is_not_consent_to_send_the_original(self, client, db_session, no_dispatch):
        """The endpoint sends what the Manažér has in front of him. Substituting Dedo's text for an empty
        box would send something he did not approve — refused instead."""
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id)

        resp = _send(client, user, version.id, proposal, text="   ")

        assert resp.status_code == 400
        assert _manazer_messages(db_session, version.id) == []
        db_session.refresh(proposal)
        assert proposal.status == "proposed"


# ── 6. handled once — sent or declined, it never comes back ───────────────────


class TestAHandledProposalIsGoneForGood:
    def test_a_sent_proposal_is_no_longer_offered_and_cannot_be_sent_twice(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        first = _send(client, user, version.id, proposal)

        assert first.status_code == 200
        assert first.json()["dedo_proposal"] is None
        db_session.refresh(proposal)
        assert proposal.status == "archived"
        assert dedo_message.open_proposal(db_session, version.id) is None
        # A second press (a double-click, a stale tab) is refused BY NAME and told why — not silently
        # applied to whatever happens to be open by then.
        again = _send(client, user, version.id, proposal)
        assert again.status_code == 409
        assert "už bol odoslaný" in again.json()["detail"]
        assert len(_manazer_messages(db_session, version.id)) == 1

    def test_a_declined_proposal_is_never_offered_and_never_delivered(self, client, db_session, no_dispatch):
        """The Manažér is not obliged to forward what Dedo found."""
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id, action="uprav")

        resp = _reject(client, user, version.id, proposal)

        assert resp.status_code == 200, resp.text
        assert resp.json()["dedo_proposal"] is None
        db_session.refresh(proposal)
        assert proposal.status == "archived"
        assert proposal.payload["dedo_proposal_resolution"] == "rejected"
        # Nothing was said to the agent, and no turn ran.
        assert _manazer_messages(db_session, version.id) == []
        assert no_dispatch == []
        assert dedo_message.pending_messages(db_session, version.id) == []

    async def test_a_declined_finding_never_appears_in_a_later_prompt(self, db_session, monkeypatch):
        """The end of the guarantee: declined is declined, including for every turn that follows."""
        _user, version, state = _build(db_session, status="agent_working")
        proposal = _propose(db_session, version.id)
        dedo_message.resolve_proposal(db_session, proposal, resolution="rejected")
        prompts = _fake_cli(monkeypatch)

        await orchestrator.run_conversation_turn(db_session, version.id)

        assert prompts and all(_FINDING not in p for p in prompts)

    def test_a_declined_proposal_keeps_dedos_words_in_the_log(self, client, db_session, no_dispatch):
        """Declining is a decision, not an erasure — the finding stays in the record."""
        user, version, _state = _build(db_session)
        proposal = _propose(db_session, version.id)

        _reject(client, user, version.id, proposal)

        db_session.refresh(proposal)
        assert proposal.content == _FINDING

    def test_declining_a_proposal_this_build_never_had_is_a_404(self, client, db_session, no_dispatch):
        user, version, _state = _build(db_session)
        resp = _reject(client, user, version.id, uuid.uuid4())
        assert resp.status_code == 404


class TestAProposalCanStartAFastFix:
    """ICCINT-54 — štvrté sloveso: návrh, ktorý nepokračuje v tejto stavbe, ale zakladá novú.

    Rýchla oprava vytvára verziu až odoslaním formulára, takže dovtedy niet stavby, do ktorej by Dedo
    text podal. Bez tohto slovesa by mu musel dať blok na skopírovanie — a to pri KAŽDEJ rýchlej oprave,
    nie raz. Návrh sa preto zavesí na najnovšiu verziu a odtiaľ sa spustí nová.
    """

    def test_the_send_creates_a_patch_version_and_starts_the_lane_with_dedos_text(
        self, client, db_session, no_dispatch
    ) -> None:
        user, version, _state = _build(db_session, version_number="0.1.0")
        proposal = _propose(db_session, version.id, content="Oprav vstupnú bránu.", action="fast_fix")

        response = _send(client, user, version.id, proposal, text="Oprav vstupnú bránu.")
        assert response.status_code == 200, response.text

        # NOVÁ verzia, nie pokračovanie tej starej.
        versions = db_session.execute(select(Version).where(Version.project_id == version.project_id)).scalars().all()
        numbers = sorted(v.version_number for v in versions)
        assert numbers == ["0.1.0", "0.1.1"], numbers

        # A beží na nej rýchla dráha s Dedovým textom ako ZADANÍM — nie ako správou.
        new_version = next(v for v in versions if v.version_number == "0.1.1")
        new_state = db_session.execute(
            select(PipelineState).where(PipelineState.version_id == new_version.id)
        ).scalar_one()
        assert new_state.flow_type == "fast_fix"
        assert any(vid == new_version.id for vid, _d in no_dispatch), "rýchla dráha sa nerozbehla"

    def test_the_old_build_is_left_alone(self, client, db_session, no_dispatch) -> None:
        """Stará stavba je hotová vec — návrh na nej nesmie nič pohnúť."""
        user, version, state = _build(db_session, version_number="0.1.0", current_stage="done", status="done")
        proposal = _propose(db_session, version.id, content="Oprav bránu.", action="fast_fix")
        before = (state.current_stage, state.status)

        assert _send(client, user, version.id, proposal, text="Oprav bránu.").status_code == 200

        db_session.refresh(state)
        assert (state.current_stage, state.status) == before

    def test_the_proposal_is_archived_so_it_cannot_start_a_second_one(self, client, db_session, no_dispatch) -> None:
        """Archivácia je v tej istej transakcii ako štart — nedá sa odoslať dvakrát a nedá sa označiť
        za odoslaný návrh, ktorý sa nerozbehol."""
        user, version, _state = _build(db_session, version_number="0.1.0")
        proposal = _propose(db_session, version.id, content="Oprav bránu.", action="fast_fix")

        assert _send(client, user, version.id, proposal, text="Oprav bránu.").status_code == 200
        second = _send(client, user, version.id, proposal, text="Oprav bránu.")
        assert second.status_code == 409, second.text

        versions = db_session.execute(select(Version).where(Version.project_id == version.project_id)).scalars().all()
        assert len(versions) == 2, "druhé odoslanie založilo ďalšiu verziu"

    def test_dedo_still_cannot_start_a_build_himself(self, client, dedo_token, db_session) -> None:
        """TOTO je hranica, na ktorej všetko stojí. Dedove dvere taký endpoint NEMAJÚ — a nesmú ho dostať
        ani omylom. Podanie návrhu je jediné, čo vie; spustenie zostáva na kliknutí Manažéra."""
        _user, version, _state = _build(db_session, version_number="0.1.0")
        db_session.commit()

        started = client.post(
            "/api/v1/pipeline/fast-fix",
            headers=_dedo_auth(),
            json={"project_id": str(version.project_id), "directive": "Skús to spustiť sám."},
        )
        assert started.status_code in (401, 403), started.text
