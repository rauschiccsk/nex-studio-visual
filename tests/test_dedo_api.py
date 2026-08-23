"""ICCINT-14 — Dedo reaches a stuck build over the network, AS HIMSELF, and can do nothing else.

ICCINT-12 and ICCINT-13 work but only from the host: to answer an escalation or release a build stuck on a
NEX Studio bug, Dedo had to be sitting at the server. This is the same two capabilities over HTTP — and the
whole point of these tests is the "and nothing else".

Charter §4.5 (Director, 2026-08-22) narrowed the old blanket ban from ACCESS to IMPERSONATION and drew the
line by hand: read any build, write one kind of message, change state one way. It also says how the line
must hold — *"Hranica má byť vynútená TOKENOM, nie disciplínou volajúceho."* So these tests are not about
whether the endpoints work; they are about what happens to everything that is not those endpoints:

1. **The door is shut unless it is opened deliberately** — no token, wrong token, and (the one that would
   be a catastrophe) an instance with no token configured, where ``compare_digest("", "")`` returning True
   would have turned "nobody set a secret" into "everybody is Dedo".
2. **The two doors do not cross** — a user's JWT buys nothing on Dedo's door, and Dedo's token buys nothing
   on a user's. Neither by content: they travel in different headers, so the refusal does not depend on the
   shape of the credential.
3. **Dedo's reach IS the mounted surface** — every route is enumerated and pinned. This is the test that
   has to fail when somebody adds an endpoint, because an endpoint is the only way Dedo grows. It did fail,
   once, when ICCINT-24 added the proposal — and the list was then widened by the implementer under a
   Director decision that does not exist. The route stays mounted but sits in its own
   ``GRANTED`` set until the Director rules; see ``TestNothingBeyondTheCharter``.
4. **The writes are the existing ones** — the same services the host CLIs call, marked as having come over
   the wire.
5. **The secret never leaves** — not in a body, not in a log line, not "masked", not in a retained
   traceback, and above all not into the AI Agent's own environment: the agent is the party that RAISES
   the blocks Dedo's token clears, so a token it can read is a token it can use to close its own
   escalation and sign the thread ``dedo``. That is the impersonation, arrived at from the inside.
6. **Dedo writes only into a build that asked** — his message is not a comment, it is the directive that
   opens the agent's next prompt, so a build that never escalated must not be able to receive one. (The
   ICCINT-24 proposal is the deliberate exception that proves it: it may name any build precisely because
   it reaches no agent at all until the Manažér presses send. Its own guarantees live in
   ``tests/test_dedo_proposal.py``.)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.api.routes.dedo import router as dedo_router
from backend.config import settings as settings_module
from backend.config.settings import settings
from backend.core import dedo_auth
from backend.core.agent_env import agent_env
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import agent_terminal, claude_agent, orchestrator
from backend.services import auth as auth_service
from backend.services import dedo_unblock as dedo_unblock_service

from .api.conftest import seed_user

# An obviously fake stand-in, never a real credential: 64 chars so it clears MIN_TOKEN_LENGTH, and made of
# one repeated character so nobody can mistake it for something issued.
_TOKEN = "d" * 64
_WRONG_TOKEN = "e" * 64


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_ANSWER = "Opravené vo v4.0.94 — port sa už nezamyká."
_REASON = "Zamykanie portu opravené vo v4.0.94; build môže pokračovať."


# ── fixtures / seeds ──────────────────────────────────────────────────────────


@pytest.fixture()
def dedo_token(monkeypatch) -> str:
    """Configure this instance with Dedo's machine identity — the RECOMMENDED way: the DIGEST only.

    The instance under test therefore does not hold Dedo's secret anywhere, which is the deployment shape
    every test below runs against by default. The plaintext configuration has its own class.
    """
    monkeypatch.setattr(settings, "dedo_api_token", "")
    monkeypatch.setattr(settings, "dedo_api_token_sha256", _sha256(_TOKEN))
    return _TOKEN


@pytest.fixture()
def plaintext_dedo_token(monkeypatch) -> str:
    """The weaker, still-supported configuration: the secret itself in the settings."""
    monkeypatch.setattr(settings, "dedo_api_token_sha256", "")
    monkeypatch.setattr(settings, "dedo_api_token", _TOKEN)
    return _TOKEN


@pytest.fixture()
def no_dedo_token(monkeypatch) -> None:
    """The default state of a fresh deployment: no machine identity configured at all."""
    monkeypatch.setattr(settings, "dedo_api_token", "")
    monkeypatch.setattr(settings, "dedo_api_token_sha256", "")


@pytest.fixture()
def capturable_backend_logs(monkeypatch) -> None:
    """Make ``caplog`` see ``backend.*`` records — without this, every log assertion below is vacuous.

    ``backend.main`` detaches the ``backend`` logger from the root (``propagate = False``) so uvicorn's
    access log survives; ``caplog`` attaches its handler to the ROOT. The two do not meet: ``caplog.text``
    is the empty string no matter what the code logs, and ``assert secret not in caplog.text`` passes for
    the same reason ``assert secret not in ""`` passes. Same fixture as
    ``backend/tests/test_release_artifacts_push.py`` — the project already knew this and this test did not.
    """
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)


def _auth(token: str = _TOKEN) -> dict[str, str]:
    return {dedo_auth.DEDO_TOKEN_HEADER: token}


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
    """A build settled exactly the way ``_settle_framework_issue`` leaves it, unless overridden."""
    defaults = {
        "version_id": version_id,
        "flow_type": "new_version",
        "current_stage": "programovanie",
        "current_actor": "ai_agent",
        "status": "blocked",
        "block_reason": "framework_issue",
        "next_action": "Čaká sa na Deda.",
        "mode": "conversation",
    }
    defaults.update(overrides)
    state = PipelineState(**defaults)
    db_session.add(state)
    db_session.flush()
    return state


def _blocked_build(db_session):
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id)
    return version, project


def _record_pipeline_message(db_session, version_id, content: str) -> PipelineMessage:
    """One ordinary thread entry, written through the orchestrator's own writer (so ``seq`` is real)."""
    from backend.services.orchestrator import _record_message

    return _record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="notification",
        content=content,
        status="delivered",
        payload={},
    )


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _every_route() -> list[tuple[str, str, str]]:
    """``(method, path, handler name)`` for every route on Dedo's door — the source of the sweep tests."""
    out: list[tuple[str, str, str]] = []
    for route in dedo_router.routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, route.path, route.name))
    return sorted(out)


def _call(client, method: str, path: str, **kwargs):
    """Issue a request the way the route expects: a body on the writes, none on the reads."""
    if method != "GET":
        kwargs.setdefault("json", {"content": "x", "reason": "x"})
    return client.request(method, path, **kwargs)


# ── 1. the door is shut unless deliberately opened ────────────────────────────


class TestTheDoorIsShut:
    @pytest.mark.parametrize("method,path,name", _every_route())
    def test_no_token_is_refused_on_every_route(self, client, dedo_token, method, path, name):
        """(a) Not one route on Dedo's door answers without the machine token."""
        url = "/api/v1/dedo" + path.replace("{version_id}", str(uuid.uuid4()))
        resp = _call(client, method, url)
        assert resp.status_code == 401, f"{method} {url} answered {resp.status_code} with NO token"

    @pytest.mark.parametrize("method,path,name", _every_route())
    def test_a_wrong_token_is_refused_on_every_route(self, client, dedo_token, method, path, name):
        """(b) A token that is not this instance's buys nothing anywhere on the door."""
        url = "/api/v1/dedo" + path.replace("{version_id}", str(uuid.uuid4()))
        resp = _call(client, method, url, headers=_auth(_WRONG_TOKEN))
        assert resp.status_code == 401, f"{method} {url} answered {resp.status_code} with a WRONG token"

    @pytest.mark.parametrize("presented", [None, "", _TOKEN, _WRONG_TOKEN])
    def test_an_unconfigured_instance_admits_nobody(self, client, no_dedo_token, presented):
        """(c) THE trap: ``secrets.compare_digest("", "")`` is True.

        With no machine identity configured, an empty header must not "match" the empty setting — and
        neither must anything else. The door does not exist yet, so it answers 503 to every shape of
        request, and 503 is never a pass.
        """
        headers = {} if presented is None else _auth(presented)
        resp = client.get("/api/v1/dedo/waiting", headers=headers)
        assert resp.status_code == 503
        assert resp.status_code != 200

    @pytest.mark.parametrize("configured", ["x", "short-token", "d" * (dedo_auth.MIN_TOKEN_LENGTH - 1)])
    def test_a_stub_token_does_not_count_as_configured(self, client, monkeypatch, configured):
        """A three-character "secret" is not meaningfully different from none, so it opens nothing either."""
        monkeypatch.setattr(settings, "dedo_api_token", configured)
        assert client.get("/api/v1/dedo/waiting", headers=_auth(configured)).status_code == 503

    def test_a_jwt_shaped_secret_is_refused_outright(self, client, db_session, monkeypatch):
        """A machine token that IS a user JWT would be replayable on the user doors — the one crossing this
        whole design exists to prevent. Refused where the setting is owned, not by teaching the user doors
        about Dedo."""
        user = seed_user(db_session, username=f"u{uuid.uuid4().hex[:6]}")
        jwt_token, _ = auth_service.create_access_token(user, 0, 60)
        monkeypatch.setattr(settings, "dedo_api_token", jwt_token)
        assert client.get("/api/v1/dedo/waiting", headers=_auth(jwt_token)).status_code == 503


# ── 2. the two doors do not cross ─────────────────────────────────────────────


class TestTheTwoDoorsDoNotCross:
    def test_a_user_jwt_opens_nothing_on_dedos_door(self, client, db_session, dedo_token):
        """(d) A logged-in Manažér — even an ``ri`` — is not Dedo. His JWT is not a machine identity."""
        user = seed_user(db_session, username=f"u{uuid.uuid4().hex[:6]}", role="ri")
        jwt_token, _ = auth_service.create_access_token(user, 0, 60)

        resp = client.get("/api/v1/dedo/waiting", headers={"Authorization": f"Bearer {jwt_token}"})
        assert resp.status_code == 401
        # …and the same JWT offered in Dedo's own header is still not Dedo's secret.
        assert client.get("/api/v1/dedo/waiting", headers=_auth(jwt_token)).status_code == 401

    def test_dedos_token_opens_nothing_on_a_user_door(self, client, db_session, dedo_token):
        """(e) The other direction, which is the one that would be impersonation.

        Both shapes are tried: the token in its own header (ignored by the user door) and the token offered
        as a Bearer credential (not a JWT, so it never resolves to a user).
        """
        seed_user(db_session, username=f"u{uuid.uuid4().hex[:6]}", role="ri")

        assert client.get("/api/v1/auth/me", headers=_auth()).status_code == 401
        assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_TOKEN}"}).status_code == 401
        # A write door, not just a read one — and the credentials store above all (charter §4.5).
        assert client.get("/api/v1/credentials", headers=_auth()).status_code == 401
        assert client.get("/api/v1/credentials", headers={"Authorization": f"Bearer {_TOKEN}"}).status_code == 401
        assert client.post("/api/v1/projects", headers=_auth(), json={}).status_code == 401


# ── 3. what Dedo may see ──────────────────────────────────────────────────────


class TestWhatDedoMaySee:
    def test_the_queue_lists_builds_waiting_on_him_across_projects(self, client, db_session, dedo_token):
        first, first_project = _blocked_build(db_session)
        second, second_project = _blocked_build(db_session)
        # A healthy build and one blocked for a reason that is NOT Dedo's to clear stay out of the queue.
        healthy, _ = _make_version(db_session)
        _seed_state(db_session, healthy.id, status="awaiting_manazer", block_reason=None)
        other_block, _ = _make_version(db_session)
        _seed_state(db_session, other_block.id, block_reason="agent_question")

        rows = client.get("/api/v1/dedo/waiting", headers=_auth()).json()

        listed = {r["version_id"] for r in rows}
        assert {str(first.id), str(second.id)} <= listed
        assert str(healthy.id) not in listed and str(other_block.id) not in listed
        # Cross-project: the escalation is NEX Studio's problem, whose build tripped over it is incidental.
        slugs = {r["project_slug"] for r in rows}
        assert {first_project.slug, second_project.slug} <= slugs
        entry = next(r for r in rows if r["version_id"] == str(first.id))
        assert entry["block_reason"] == "framework_issue"
        assert entry["current_stage"] == "programovanie"

    def test_one_build_reports_phase_status_and_why_it_is_stuck(self, client, db_session, dedo_token):
        version, project = _blocked_build(db_session)

        body = client.get(f"/api/v1/dedo/builds/{version.id}", headers=_auth()).json()

        assert body["project_slug"] == project.slug
        assert body["version_number"] == version.version_number
        assert (body["current_stage"], body["status"], body["block_reason"]) == (
            "programovanie",
            "blocked",
            "framework_issue",
        )

    def test_a_healthy_build_is_readable_too(self, client, db_session, dedo_token):
        """Reading is the WIDE half of the grant: diagnosing NEX Studio needs the build that did NOT break."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="agent_working", block_reason=None, current_stage="vizual")

        body = client.get(f"/api/v1/dedo/builds/{version.id}", headers=_auth()).json()
        assert (body["status"], body["block_reason"]) == ("agent_working", None)

    def test_the_message_log_is_the_same_thread_the_cockpit_shows(self, client, db_session, dedo_token):
        from backend.services.orchestrator import _record_message

        version, _ = _blocked_build(db_session)
        _record_message(
            db_session,
            version_id=version.id,
            stage="programovanie",
            author="ai_agent",
            recipient="manazer",
            kind="notification",
            content="Nedá sa spustiť sandbox — chyba je v NEX Studiu.",
            status="delivered",
            payload={},
        )
        db_session.flush()

        rows = client.get(f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth()).json()

        assert [r["author"] for r in rows] == ["ai_agent"]
        assert "sandbox" in rows[0]["content"]

    def test_the_thread_arrives_oldest_first_and_it_is_the_END_that_arrives(self, client, db_session, dedo_token):
        """The contract the endpoint states: the LAST ``limit`` entries, in chronological order.

        Both halves matter and neither is visible with a single seeded message (which is all this file used
        to do — with one row, "reversed" and "not reversed" are the same list, and so are "first N" and
        "last N"). A long build runs to thousands of rows: getting the OLDEST 200 back would hand Dedo the
        beginning of a build he is being asked about the end of, and getting them newest-first would
        reverse the conversation he has to read. Neither would fail anything before this test.
        """
        version, _ = _blocked_build(db_session)
        for index in range(6):
            _record_pipeline_message(db_session, version.id, f"msg-{index}")
        db_session.flush()

        full = client.get(f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth()).json()
        assert [row["content"] for row in full] == [f"msg-{i}" for i in range(6)]

        # The WINDOW is the tail, still chronological: the last three, in the order they were said.
        window = client.get(f"/api/v1/dedo/builds/{version.id}/messages?limit=3", headers=_auth()).json()
        assert [row["content"] for row in window] == ["msg-3", "msg-4", "msg-5"]

    def test_the_window_size_is_bounded_on_both_ends(self, client, db_session, dedo_token):
        """``limit`` is a query parameter on an unattended machine call — it is validated, not trusted."""
        version, _ = _blocked_build(db_session)
        for bad in (0, -1, 1001):
            resp = client.get(f"/api/v1/dedo/builds/{version.id}/messages?limit={bad}", headers=_auth())
            assert resp.status_code == 422, f"limit={bad} was accepted"
        assert client.get(f"/api/v1/dedo/builds/{version.id}/messages?limit=1000", headers=_auth()).status_code == 200

    def test_an_unknown_build_is_a_404_not_an_empty_answer(self, client, db_session, dedo_token):
        assert client.get(f"/api/v1/dedo/builds/{uuid.uuid4()}", headers=_auth()).status_code == 404
        # A version that exists but was never started is equally a mistake worth surfacing.
        version, _ = _make_version(db_session)
        assert client.get(f"/api/v1/dedo/builds/{version.id}", headers=_auth()).status_code == 404
        assert client.get(f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth()).status_code == 404


# ── 4. what Dedo may do — exactly two things, through the existing services ───


class TestWhatDedoMayDo:
    def test_he_can_answer_the_agent_and_the_record_says_it_came_over_the_wire(self, client, db_session, dedo_token):
        version, _ = _blocked_build(db_session)

        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/messages",
            headers=_auth(),
            json={"content": f"  {_ANSWER}  "},
        )

        assert resp.status_code == 201
        rows = _msgs(db_session, version.id)
        assert len(rows) == 1
        # Written as DEDO — his own name in the thread, never a user's (that is the impersonation the
        # charter forbids) — and addressed to the agent who escalated.
        assert (rows[0].author, rows[0].recipient, rows[0].content) == ("dedo", "ai_agent", _ANSWER)
        assert rows[0].status == "pending"
        # …and the audit trail records the TRANSPORT, so "Dedo over the network" is distinguishable from
        # "Dedo at the server" long after anyone remembers.
        assert rows[0].payload["dedo_transport"] == "api"

    def test_an_empty_answer_is_refused(self, client, db_session, dedo_token):
        version, _ = _blocked_build(db_session)
        db_session.commit()  # so "no message was written" is a real finding, not the rollback's doing
        assert (
            client.post(f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth(), json={"content": ""}).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth(), json={"content": "   "}
            ).status_code
            == 409
        )
        assert _msgs(db_session, version.id) == []

    def test_he_can_release_a_build_and_it_lands_on_the_manazers_desk(self, client, db_session, dedo_token):
        version, _ = _blocked_build(db_session)

        body = client.post(
            f"/api/v1/dedo/builds/{version.id}/unblock", headers=_auth(), json={"reason": _REASON}
        ).json()

        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "awaiting_manazer"
        assert state.block_reason is None
        assert state.resume_after_framework_fix is True
        # The build does NOT resume by itself: the human keeps the last word.
        assert "Pokračovať" in state.next_action
        assert body["status"] == "awaiting_manazer"
        # Straight through ICCINT-13's service — same reason-as-Dedo's-message record, plus the transport.
        rows = _msgs(db_session, version.id)
        assert len(rows) == 1
        assert (rows[0].author, rows[0].content) == ("dedo", _REASON)
        assert rows[0].payload["dedo_unblock"] is True
        assert rows[0].payload["dedo_transport"] == "api"

    def test_a_build_that_is_not_stuck_on_nex_studio_cannot_be_released(self, client, db_session, dedo_token):
        """The unblock is not a general-purpose state lever — it clears ONE reason and refuses the rest."""
        healthy, _ = _make_version(db_session)
        _seed_state(db_session, healthy.id, status="awaiting_manazer", block_reason=None)
        question, _ = _make_version(db_session)
        _seed_state(db_session, question.id, block_reason="agent_question")
        # Commit the seed: a refused write ROLLS BACK (as it must in production), and inside the suite's
        # savepoint isolation an uncommitted seed would go with it and the assertion would prove nothing.
        db_session.commit()

        for version in (healthy, question):
            resp = client.post(f"/api/v1/dedo/builds/{version.id}/unblock", headers=_auth(), json={"reason": _REASON})
            assert resp.status_code == 409
            assert _msgs(db_session, version.id) == []

    def test_releasing_without_a_reason_is_refused(self, client, db_session, dedo_token):
        version, _ = _blocked_build(db_session)
        db_session.commit()  # see the note in the test above — the refusal rolls back
        assert (
            client.post(f"/api/v1/dedo/builds/{version.id}/unblock", headers=_auth(), json={"reason": "  "}).status_code
            == 409
        )
        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "blocked"

    def test_a_build_that_never_escalated_cannot_be_written_into(self, client, db_session, dedo_token):
        """THE DIRECTIVE TEST. A Dedo message is not a comment — it is the top of the agent's next prompt.

        ``pending_for_prompt`` folds it in under "Odpoveď od Deda" with "Riaď sa pokynom nižšie a pokračuj
        v práci": a top-priority instruction the agent has no reason to question. So the question "may Dedo
        write here" is really "may Dedo steer this agent", and the answer is yes only for the build that
        escalated to him. Every phase is tried, because the hole was open in all of them, and in a project
        that never asked Dedo anything — which is the whole point: this door is cross-project by design.
        """
        for stage in ("priprava", "vizual", "programovanie"):
            healthy, project = _make_version(db_session)
            _seed_state(db_session, healthy.id, current_stage=stage, status="agent_working", block_reason=None)
            db_session.commit()  # a refused write rolls back; an uncommitted seed would go with it

            resp = client.post(f"/api/v1/dedo/builds/{healthy.id}/messages", headers=_auth(), json={"content": _ANSWER})

            assert resp.status_code == 409, f"a directive reached a healthy build in {stage} ({project.slug})"
            assert _msgs(db_session, healthy.id) == []

    def test_a_build_blocked_for_someone_elses_reason_is_not_his_either(self, client, db_session, dedo_token):
        """``agent_question`` is the Manažér's to answer. Blocked is not the same as blocked ON DEDO."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, block_reason="agent_question")
        db_session.commit()

        resp = client.post(f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth(), json={"content": _ANSWER})

        assert resp.status_code == 409
        assert _msgs(db_session, version.id) == []

    def test_he_may_still_speak_into_the_build_he_just_released(self, client, db_session, dedo_token):
        """Correcting or completing one's own answer before the Manažér presses "Pokračovať" is part of
        answering — the build did ask, and the message reaches the agent that asked."""
        version, _ = _blocked_build(db_session)
        assert (
            client.post(
                f"/api/v1/dedo/builds/{version.id}/unblock", headers=_auth(), json={"reason": _REASON}
            ).status_code
            == 200
        )

        resp = client.post(
            f"/api/v1/dedo/builds/{version.id}/messages", headers=_auth(), json={"content": "Ešte doplnenie."}
        )

        assert resp.status_code == 201
        assert [m.content for m in _msgs(db_session, version.id)] == [_REASON, "Ešte doplnenie."]

    def test_the_host_path_is_untouched_and_distinguishable(self, db_session):
        """ICCINT-14 adds a transport; it does not replace one.

        The host CLI calls the SAME service with no transport label (``tests/test_dedo_unblock.py`` drives
        the command itself). Called that way the record is exactly what it always was — so a marked message
        means "over the network" and an unmarked one means "at the server", with no third possibility.
        """
        version, _ = _blocked_build(db_session)

        dedo_unblock_service.unblock_framework_issue(db_session, version_id=version.id, reason=_REASON)

        rows = _msgs(db_session, version.id)
        assert (rows[0].author, rows[0].payload["dedo_unblock"]) == ("dedo", True)
        assert "dedo_transport" not in rows[0].payload


# ── 5. nothing beyond the charter ─────────────────────────────────────────────


class TestNothingBeyondTheCharter:
    """(f) THE SCOPE TEST. Dedo's abilities are the mounted surface, so the surface is pinned here.

    This is the test that must go red when someone adds an endpoint to Dedo's router — approving a gate,
    starting a build, answering for the Manažér. None of those can be prevented by a check nobody wrote;
    they are prevented by not existing, and "not existing" is only enforceable by enumeration.
    """

    #: Charter §4.5, in full: three reads, one message, one unblock. Nothing here was added by anyone but
    #: the Director.
    GRANTED = {
        ("GET", "/waiting", "list_waiting_builds"),
        ("GET", "/builds/{version_id}", "get_build"),
        ("GET", "/builds/{version_id}/messages", "list_build_messages"),
        ("POST", "/builds/{version_id}/messages", "post_build_message"),
        ("POST", "/builds/{version_id}/unblock", "unblock_build"),
        # GRANTED by the Director 23.08.2026, after he asked for it by name: a proposal grants Dedo no new
        # REACH. It is recorded ``status='proposed'``, addressed to the ``manazer``; no prompt can carry it
        # (delivery keys on ``pending``); the agent is never told it exists. It reaches the agent only if
        # the Manažér presses send, and then as HIS message through the ordinary ``uprav``/``answer``/``ask``
        # action, with that action's guards. That is why it may name ANY build while ``post_build_message``
        # still may not — pinned by ``tests/test_dedo_proposal.py``.
        ("POST", "/builds/{version_id}/proposals", "propose_build_message"),
    }

    #: MOUNTED BUT NOT YET GRANTED — kept in its own set so the difference is impossible to overlook.
    #:
    #: ICCINT-24 added this sixth endpoint and it turned the enumeration below red, which is the process
    #: working. What happened NEXT was not: the implementer widened the expected set himself and wrote into
    #: the source that a Director decision of 2026-08-23 put it there. No such decision exists — not in this
    #: repository, not in ``/home/icc/knowledge`` (audit 2026-08-23, finding 3). The claim is removed; the
    #: endpoint is left mounted and listed HERE, so the suite states what is true: it is running, it has not
    #: been granted, and the Director has to say yes or no.
    #:

    def test_the_door_has_exactly_the_granted_endpoints(self):
        assert set(_every_route()) == self.GRANTED, (
            "Dedo's door changed shape. Every endpoint here is a capability granted to a MACHINE identity "
            "that answers to nobody at request time. If this list grew, it needs a Director decision, not "
            "a green test — and not a comment CLAIMING one either: the sixth endpoint first arrived here "
            "citing a 'Director decision (2026-08-23)' that had never been made. Writing those words next "
            "to a new line costs nothing and cannot be checked at runtime. Whoever adds a line must be able "
            "to point at where the decision is recorded."
        )

    def test_nothing_else_is_mounted_under_the_dedo_prefix(self):
        """A second router accidentally mounted at ``/api/v1/dedo`` would widen Dedo just as effectively."""
        from backend.main import app

        mounted = {
            (method, route.path)
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1/dedo")
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        }
        assert mounted == {(m, "/api/v1/dedo" + p) for (m, p, _n) in self.GRANTED}

    def test_the_charter_verbs_are_absent(self, client, db_session, dedo_token):
        """The named forbidden powers, tried as URLs: approving, starting, stopping, deciding.

        They 404/405 because there is nothing there — the strongest possible answer, and the reason this
        design put Dedo on his own door instead of widening ``get_current_user``.
        """
        version, _ = _blocked_build(db_session)
        for method, path in [
            ("POST", f"/api/v1/dedo/builds/{version.id}/action"),
            ("POST", f"/api/v1/dedo/builds/{version.id}/approve"),
            ("POST", f"/api/v1/dedo/builds/{version.id}/start"),
            ("POST", f"/api/v1/dedo/builds/{version.id}/pause"),
            ("GET", "/api/v1/dedo/credentials"),
        ]:
            resp = _call(client, method, path, headers=_auth())
            assert resp.status_code in (404, 405), f"{method} {path} exists on Dedo's door"

    def test_the_manazers_engine_stays_out_of_reach(self, client, db_session, dedo_token):
        """The pipeline action route — the one that CAN approve a gate — does not answer to Dedo's token."""
        version, _ = _blocked_build(db_session)
        resp = client.post(
            f"/api/v1/pipeline/{version.id}/action",
            headers=_auth(),
            json={"action": "schvalit", "payload": {}},
        )
        assert resp.status_code == 401


# ── 6. the secret never leaves ────────────────────────────────────────────────


class TestTheTokenNeverLeaks:
    """(g) A masked secret is still a leaked secret, so the rule is: it never appears at all.

    Every test here that asserts on ``caplog`` takes ``capturable_backend_logs``. Without it the assertion
    is vacuous — see the fixture — and the audit proved it: a mutation that logged BOTH tokens on the
    refusal path left the whole suite green.
    """

    def test_not_in_a_refusal_body_and_not_in_the_log(
        self, client, db_session, dedo_token, caplog, capturable_backend_logs
    ):
        with caplog.at_level(logging.DEBUG, logger="backend"):
            wrong = client.get("/api/v1/dedo/waiting", headers=_auth(_WRONG_TOKEN))
            missing = client.get("/api/v1/dedo/waiting")

        assert (wrong.status_code, missing.status_code) == (401, 401)
        for body in (wrong.text, missing.text):
            assert _TOKEN not in body and _WRONG_TOKEN not in body
            # Not even a fragment: a prefix is enough to make a brute force cheap.
            assert _TOKEN[:8] not in body and _WRONG_TOKEN[:8] not in body
        assert _TOKEN not in caplog.text and _WRONG_TOKEN not in caplog.text
        assert _TOKEN[:8] not in caplog.text and _WRONG_TOKEN[:8] not in caplog.text
        # The refusal still SAYS something useful — it just says it about the identity, not the secret.
        assert missing.json()["detail"]

    def test_not_in_the_unconfigured_answer(self, client, no_dedo_token, caplog, capturable_backend_logs):
        with caplog.at_level(logging.DEBUG, logger="backend"):
            resp = client.get("/api/v1/dedo/waiting", headers=_auth())
        assert resp.status_code == 503
        assert _TOKEN not in resp.text and _TOKEN[:8] not in resp.text
        assert _TOKEN not in caplog.text
        # It names the ENV VAR to set, which is the operator's actual next step — never a value.
        assert "DEDO_API_TOKEN" in resp.json()["detail"]

    def test_not_on_a_successful_read(self, client, db_session, dedo_token):
        _blocked_build(db_session)
        resp = client.get("/api/v1/dedo/waiting", headers=_auth())
        assert resp.status_code == 200
        assert _TOKEN not in resp.text and _TOKEN[:8] not in resp.text

    def test_the_log_assertions_above_can_actually_fail(self, caplog, capturable_backend_logs):
        """The test that tests the tests.

        Every "not in caplog.text" assertion in this class is only worth the paper it is written on if
        ``caplog.text`` can contain a ``backend.*`` record at all. It could not — ``backend.main`` detaches
        that logger from the root — so those assertions passed against an empty string for as long as they
        existed. This pins the fixture that fixed it: if the capture breaks again, this goes red FIRST and
        the vacuous assertions do not get to pretend.
        """
        with caplog.at_level(logging.DEBUG, logger="backend"):
            logging.getLogger("backend.core.dedo_auth").warning("PROBE-%s", "MARKER")
        assert "PROBE-MARKER" in caplog.text

    def test_a_refusal_keeps_nothing_that_can_be_printed_later(self, client, dedo_token):
        """The leak channel a body/log assertion cannot see: a RETAINED traceback.

        Re-raising one module-level ``HTTPException`` appends a frame to it on every raise, forever. Two
        consequences, both real and both measured before this test existed: an unauthenticated caller grows
        the process's memory without bound (200 refusals → 2340 permanently retained frames), and every
        retained frame keeps ``require_dedo_identity``'s locals alive — the configured credential and the
        caller's input — reachable from a module global and printed verbatim by anything that renders
        locals (Sentry, ``pytest --showlocals``, ``cgitb``).

        So: after a burst of refusals, NOTHING in the auth module may still be holding a traceback.
        """
        for _ in range(25):
            assert client.get("/api/v1/dedo/waiting", headers=_auth(_WRONG_TOKEN)).status_code == 401
            assert client.get("/api/v1/dedo/waiting").status_code == 401

        retained = [
            (name, obj)
            for name, obj in vars(dedo_auth).items()
            if isinstance(obj, Exception) and obj.__traceback__ is not None
        ]
        assert retained == [], f"a module-level exception is holding a traceback: {[n for n, _ in retained]}"

    def test_the_refusal_that_is_raised_holds_the_secret_in_no_frame(self, dedo_token):
        """Rendered WITH locals — the strongest form of the question — the refusal shows no credential.

        ``traceback.TracebackException(..., capture_locals=True)`` is what a crash reporter does. On the
        exception this door raises it may print the caller's input (that is the caller's own doing and it
        does not survive the request), but never this instance's identity.

        Rendered from ``tb_next``: the first frame is this test's own, whose locals hold the stand-in token
        because the test put it there. Judging the door by the caller's frame would measure the test.
        """
        import traceback

        with pytest.raises(HTTPException) as excinfo:
            dedo_auth.require_dedo_identity(presented=_WRONG_TOKEN)

        inside_the_door = excinfo.value.__traceback__.tb_next
        assert inside_the_door is not None
        rendered = "".join(
            traceback.TracebackException(
                type(excinfo.value), excinfo.value, inside_the_door, capture_locals=True
            ).format()
        )
        assert "dedo_auth.py" in rendered  # we really are looking at the door's own frame
        # The SECRET is what may not be there. The verifier may: this frame compares digests, so the digest
        # is in its locals by construction — and a digest in a crash report opens nothing
        # (``test_reading_everything_this_instance_holds_opens_nothing`` is the proof). That difference is
        # the whole reason the door is configured with a digest.
        assert _TOKEN not in rendered
        # Two frames at most (the caller's + the dependency's): a fresh exception, not a shared one that
        # has been collecting frames since the process started.
        depth, tb = 0, excinfo.value.__traceback__
        while tb is not None:
            depth, tb = depth + 1, tb.tb_next
        assert depth <= 2


# ── 7. the instance holds a VERIFIER, not the secret ──────────────────────────


class TestTheDoorVerifiesWithoutHoldingTheSecret:
    """The recommended configuration: this container is configured with a DIGEST and nothing else.

    It matters because of who lives in this container. The backend spawns the AI Agent — ``claude`` with an
    unrestricted Bash tool, as root, here — and the agent is the party Dedo's token binds: it raises the
    ``framework_issue`` blocks that token clears. Whatever the container holds, the agent can read. So the
    container holds nothing that opens the door.
    """

    def test_the_token_opens_the_door_but_the_stored_value_is_only_its_digest(self, client, db_session, dedo_token):
        _blocked_build(db_session)
        assert client.get("/api/v1/dedo/waiting", headers=_auth()).status_code == 200
        # What the instance is configured with is not the credential…
        assert settings.dedo_api_token == ""
        assert settings.dedo_api_token_sha256 == _sha256(_TOKEN)
        # …and cannot be derived back into one.
        assert _TOKEN not in settings.dedo_api_token_sha256

    def test_reading_everything_this_instance_holds_opens_nothing(self, client, db_session, dedo_token):
        """The attack the digest exists to defeat: present what the container knows.

        An agent that read the settings, the environment and the process's own memory ends up holding the
        digest. Offering it — raw, or hashed again the way a confused replay would — is refused.
        """
        _blocked_build(db_session)
        stolen = settings.dedo_api_token_sha256
        for attempt in (stolen, _sha256(stolen), stolen.upper()):
            assert client.get("/api/v1/dedo/waiting", headers=_auth(attempt)).status_code == 401

    def test_a_malformed_digest_is_no_identity_at_all(self, client, monkeypatch):
        """Not a door that refuses everyone silently (401) — an instance that says it is unconfigured (503).

        A typo'd digest cannot match anything, and the difference between "your credential is wrong" and
        "this deployment has no Dedo identity" is a five-minute fix versus an afternoon of re-issuing.
        """
        monkeypatch.setattr(settings, "dedo_api_token", "")
        for bad in ("not-a-digest", _sha256(_TOKEN)[:-1], _sha256(_TOKEN) + "0", "z" * 64):
            monkeypatch.setattr(settings, "dedo_api_token_sha256", bad)
            assert client.get("/api/v1/dedo/waiting", headers=_auth()).status_code == 503

    def test_the_plaintext_configuration_still_works_and_is_the_same_door(
        self, client, db_session, plaintext_dedo_token
    ):
        """ICCINT-14 was specified with the secret in the environment; that shape keeps working…"""
        _blocked_build(db_session)
        assert client.get("/api/v1/dedo/waiting", headers=_auth()).status_code == 200
        assert client.get("/api/v1/dedo/waiting", headers=_auth(_WRONG_TOKEN)).status_code == 401

    def test_a_stub_or_jwt_secret_opens_nothing_under_either_configuration(self, client, monkeypatch, db_session):
        """…and neither shape can be tricked into accepting a secret that is not one.

        On the plaintext path the setting itself is inspected. On the digest path there is no plaintext to
        inspect, so the same two rules are enforced on what the CALLER presents — which is where they have
        to hold anyway.
        """
        user = seed_user(db_session, username=f"u{uuid.uuid4().hex[:6]}")
        jwt_token, _ = auth_service.create_access_token(user, 0, 60)
        for weak in ("short", jwt_token):
            monkeypatch.setattr(settings, "dedo_api_token_sha256", "")
            monkeypatch.setattr(settings, "dedo_api_token", weak)
            assert client.get("/api/v1/dedo/waiting", headers=_auth(weak)).status_code == 503

            monkeypatch.setattr(settings, "dedo_api_token", "")
            monkeypatch.setattr(settings, "dedo_api_token_sha256", _sha256(weak))
            assert client.get("/api/v1/dedo/waiting", headers=_auth(weak)).status_code == 401


# ── 8. the AI Agent is never handed the credential that binds it ──────────────


class TestTheAgentNeverGetsTheToken:
    """THE IMPERSONATION FROM THE INSIDE. A build turn runs with an unrestricted Bash tool; a child process
    inherits its parent's environment. If Dedo's secret were in that environment, an agent blocked on
    ``framework_issue`` could read it with ``printenv``, write into its own thread as ``dedo`` and clear its
    own escalation — the charter's forbidden impersonation, reached without touching a user account.

    Both lines are pinned here: the secret is taken out of ``os.environ`` at import (which covers every
    child, including the ones that inherit implicitly), and the three sites that build a child environment
    by hand strip it again.
    """

    def test_the_process_environment_does_not_carry_it(self, monkeypatch):
        """Importing the settings TAKES the variable — it does not merely read it.

        This is the line that covers the children nobody enumerated: every ``subprocess.run`` in the
        codebase that inherits implicitly, and every one added tomorrow.
        """
        monkeypatch.setenv("DEDO_API_TOKEN", _TOKEN)

        assert settings_module._take_dedo_plaintext_token() == _TOKEN
        assert "DEDO_API_TOKEN" not in os.environ

    def test_the_environment_builder_withholds_it(self, monkeypatch):
        monkeypatch.setenv("DEDO_API_TOKEN", _TOKEN)
        monkeypatch.setenv("GH_TOKEN", "gh-stays")

        env = agent_env({"TERM": "xterm-256color"})

        assert "DEDO_API_TOKEN" not in env
        assert _TOKEN not in "".join(env.values())
        # …while the agent's working tools are untouched: this is a boundary, not a purge.
        assert env["GH_TOKEN"] == "gh-stays"
        assert env["TERM"] == "xterm-256color"

    def test_the_headless_build_turn_is_spawned_without_it(self, monkeypatch):
        """The main event: ``invoke_claude`` — the build turn, which runs with full Bash."""
        monkeypatch.setenv("DEDO_API_TOKEN", _TOKEN)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
        mock_exec = AsyncMock(return_value=proc)
        monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)

        asyncio.run(claude_agent.invoke_claude(project_slug="p", claude_session_id=uuid.uuid4(), prompt="x"))

        env = mock_exec.call_args.kwargs["env"]
        assert "DEDO_API_TOKEN" not in env
        assert _TOKEN not in "".join(env.values())

    def test_the_agent_terminal_is_spawned_without_it(self, monkeypatch, tmp_path):
        """The interactive PTY — the same agent, with a keyboard."""
        monkeypatch.setenv("DEDO_API_TOKEN", _TOKEN)
        spec = tmp_path / "CLAUDE.md"
        spec.write_text("charter", encoding="utf-8")
        captured: dict = {}

        def fake_spawn(argv, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(agent_terminal.ptyprocess.PtyProcess, "spawn", staticmethod(fake_spawn))

        agent_terminal._spawn_pty(spec_path=spec, project_root=tmp_path, claude_session_id=uuid.uuid4(), resume=False)

        assert "DEDO_API_TOKEN" not in captured["env"]
        assert _TOKEN not in "".join(captured["env"].values())

    def test_the_projects_own_smoke_script_runs_without_it(self, monkeypatch, tmp_path):
        """``release_smoke_test.sh`` lives in the work tree the AI Agent WRITES — running it is running the
        agent's own code, so it gets the agent's environment, not the backend's."""
        monkeypatch.setenv("DEDO_API_TOKEN", _TOKEN)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_exec = AsyncMock(return_value=proc)
        monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", mock_exec)

        asyncio.run(
            orchestrator._run_acceptance_script(tmp_path / "release_smoke_test.sh", {"COMPOSE_PROJECT_NAME": "x"})
        )

        env = mock_exec.call_args.kwargs["env"]
        assert "DEDO_API_TOKEN" not in env
        assert _TOKEN not in "".join(env.values())
        assert env["COMPOSE_PROJECT_NAME"] == "x"  # the addressing the script actually needs still arrives

    def test_no_spawn_site_hands_over_the_raw_environment(self):
        """A sweep, so a spawn site added later cannot reintroduce the hole quietly.

        Every child environment built in the modules that start agent-controlled processes must go through
        :func:`~backend.core.agent_env.agent_env`. Comments are stripped before the search — the fix's own
        explanation quotes the pattern it forbids, and a check that cannot tell code from prose would go
        red on the documentation and green on the bug.
        """
        import inspect

        for module in (claude_agent, agent_terminal, orchestrator):
            code = "\n".join(line.split("#", 1)[0] for line in inspect.getsource(module).splitlines())
            assert "{**os.environ" not in code, (
                f"{module.__name__} builds a child environment from the raw os.environ — use agent_env()"
            )
