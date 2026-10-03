"""A Manažér action whose dispatch cannot start is refused, never recorded as sent (ICCINT-163).

``schedule_dispatch`` is single-flight in memory: while a dispatch for the version runs, it logs "already
in-flight — skipping duplicate" and returns — and the action's directive (the Manažér's own words) goes with
it. The durable ``dispatch_in_flight`` flag was meant to keep any action from getting that far, but a flag can
drop before the runner's task really ends: the pause did exactly that on dedo-home 03.10.2026, and every
ordinary settle leaves a shorter window of its own (the runner still broadcasts, notifies and cleans up after
the status write). In that window an "Odpovedať" or "Vrátiť agentovi na doplnenie" was accepted, written down
as delivered, and never reached the agent.

The route therefore asks the runner itself — the one that would have to start the turn — and gives a turn
that is merely finishing up a short grace to end.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User, UserSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import auth as auth_service
from backend.services import dedo_message, pipeline_runner


@pytest.fixture()
def scheduled(monkeypatch) -> list:
    """Capture the background dispatch instead of running an agent."""
    calls: list = []

    def _capture(version_id, directive=None):
        calls.append((version_id, directive))

    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", _capture)
    return calls


@pytest.fixture()
def short_grace(monkeypatch):
    monkeypatch.setattr(pipeline_runner, "SETTLE_GRACE_SECONDS", 0.2, raising=False)


def _make_user(db_session) -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserSession(user_id=user.id, token_version=0))
    db_session.flush()
    return user


def _paused_build(db_session, user: User) -> Version:
    """The state the screen showed during the incident: 'paused', flag already dropped."""
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="paused",
            pause_reason="manazer",
            next_action="Pozastavené Manažérom — pokračuj cez 'Pokračovať'.",
            dispatch_in_flight=False,
        )
    )
    db_session.commit()
    return version


def _bearer(user: User) -> dict[str, str]:
    token, _ = auth_service.create_access_token(user, 0, 60)
    return {"Authorization": f"Bearer {token}"}


def _manazer_messages(db_session, version_id) -> list[PipelineMessage]:
    return list(
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
        ).scalars()
    )


def _start_runner_task(client, version_id, *, finishes_after: Optional[float]) -> asyncio.Task:
    """Register a dispatch on the app's own event loop, exactly where ``schedule_dispatch`` puts one."""

    async def _register() -> asyncio.Task:
        async def _work() -> None:
            if finishes_after is None:
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(finishes_after)

        task = asyncio.get_running_loop().create_task(_work())
        pipeline_runner._ACTIVE_DISPATCH[version_id] = task
        return task

    return client.portal.call(_register)


def _stop_runner_task(client, version_id, task: asyncio.Task) -> None:
    async def _cancel() -> None:
        task.cancel()
        if pipeline_runner._ACTIVE_DISPATCH.get(version_id) is task:
            del pipeline_runner._ACTIVE_DISPATCH[version_id]

    client.portal.call(_cancel)


def test_a_steer_while_the_runner_still_works_is_refused_and_not_recorded(client, db_session, scheduled, short_grace):
    user = _make_user(db_session)
    version = _paused_build(db_session, user)
    task = _start_runner_task(client, version.id, finishes_after=None)
    try:
        res = client.post(
            f"/api/v1/pipeline/{version.id}/action",
            headers=_bearer(user),
            json={"action": "uprav", "payload": {"comment": "zrýchli skúšky"}},
        )
    finally:
        _stop_runner_task(client, version.id, task)

    assert res.status_code == 400, res.text
    assert "AI Agent ešte pracuje" in res.json()["detail"]
    assert _manazer_messages(db_session, version.id) == [], "a steer nobody will carry must not look sent"
    assert scheduled == []


def test_dedos_proposal_sent_while_the_runner_works_stays_open(client, db_session, scheduled, short_grace):
    user = _make_user(db_session)
    version = _paused_build(db_session, user)
    proposal = dedo_message.record_dedo_proposal(
        db_session, version_id=version.id, content="zrýchli skúšky", proposed_action="uprav"
    )
    db_session.commit()
    task = _start_runner_task(client, version.id, finishes_after=None)
    try:
        res = client.post(
            f"/api/v1/pipeline/{version.id}/dedo-proposal/send",
            headers=_bearer(user),
            json={"message_id": str(proposal.id), "text": "zrýchli skúšky"},
        )
    finally:
        _stop_runner_task(client, version.id, task)

    assert res.status_code == 400, res.text
    db_session.refresh(proposal)
    assert proposal.status == "proposed", "the finding must still be on his desk, not archived as sent"
    assert _manazer_messages(db_session, version.id) == []


def test_a_runner_that_is_only_finishing_up_is_waited_for(client, db_session, scheduled, monkeypatch):
    # The ordinary settle window: status already written, the runner's task still broadcasting/notifying.
    # A short grace absorbs it, so a quick answer is not bounced for nothing.
    monkeypatch.setattr(pipeline_runner, "SETTLE_GRACE_SECONDS", 5.0, raising=False)
    user = _make_user(db_session)
    version = _paused_build(db_session, user)
    task = _start_runner_task(client, version.id, finishes_after=0.2)
    try:
        res = client.post(
            f"/api/v1/pipeline/{version.id}/action",
            headers=_bearer(user),
            json={"action": "uprav", "payload": {"comment": "zrýchli skúšky"}},
        )
    finally:
        _stop_runner_task(client, version.id, task)

    assert res.status_code == 200, res.text
    assert [m.content for m in _manazer_messages(db_session, version.id)] == ["zrýchli skúšky"]
    assert len(scheduled) == 1 and "zrýchli skúšky" in (scheduled[0][1] or "")


def test_pressing_pause_does_not_try_to_start_a_second_dispatch(client, db_session, scheduled):
    # A pause that stays agent_working (the request) must not fall into the "left an agent working →
    # schedule it" branch: that only produced a "skipping duplicate" warning in the log on every pause.
    user = _make_user(db_session)
    version = _paused_build(db_session, user)
    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    state.status = "agent_working"
    state.dispatch_in_flight = True
    db_session.commit()

    res = client.post(f"/api/v1/pipeline/{version.id}/action", headers=_bearer(user), json={"action": "pause"})

    assert res.status_code == 200, res.text
    assert scheduled == []
