"""CR-NS-074 — Class J fix: the out-of-band Telegram nudge targets the AWAY Director(s)
on the open board (their OWN chat_id), not the (often null) project owner.

Regression for the 2026-06-13 incident: every cockpit project except nex-ledger had a NULL
``owner_id`` → ``_owner_chat_id`` returned ``None`` → the away Director got NO Telegram ping
even though his user row carried a ``telegram_chat_id``. The fix resolves the recipient from
the registry's away-toggled ``user_id``(s); the owner stays a fallback for the fully-absent case.
"""

from __future__ import annotations

import types
import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import pipeline_runner
from backend.services.pipeline_ws import registry


@pytest.mark.asyncio
async def test_away_director_ids_tracks_away_flag() -> None:
    """Registry exposes the away-toggled user ids — the Class J recipient source."""
    vid = uuid.uuid4()
    away_ws, present_ws = object(), object()
    away_uid, present_uid = uuid.uuid4(), uuid.uuid4()
    await registry.connect(vid, away_ws, away_uid)
    await registry.connect(vid, present_ws, present_uid)
    await registry.set_away(vid, away_ws, True)
    try:
        assert registry.away_director_ids(vid) == {away_uid}
        # the away socket drops out of the "active" gate, the present one stays
        assert registry.active_director_ids(vid) == {present_uid}
    finally:
        await registry.disconnect(vid, away_ws)
        await registry.disconnect(vid, present_ws)


@pytest.mark.asyncio
async def test_away_director_pinged_when_project_has_no_owner(db_session, monkeypatch) -> None:
    """The away Director's OWN chat is pinged even when the project has no ``owner_id`` (the live bug)."""
    director = User(
        username="away-director",
        email="away-director@isnex.ai",
        password_hash="hashed-placeholder",
        role="ri",
        telegram_chat_id="999777",
    )
    db_session.add(director)
    db_session.flush()

    # The live reality at the incident: cockpit project with NO owner.
    project = Project(
        name="Ownerless Proj",
        slug="ownerless-proj",
        type="standard",
        auth_mode="password",
        description="Class J regression fixture.",
        created_by=director.id,
    )
    db_session.add(project)
    db_session.flush()
    assert project.owner_id is None

    version = Version(project_id=project.id, version_number="v1.0.0", status="planned")
    db_session.add(version)
    db_session.flush()

    ws = object()
    await registry.connect(version.id, ws, director.id)
    await registry.set_away(version.id, ws, True)

    sent: list[tuple[str, str]] = []

    async def fake_send(message: str, chat_id: str) -> None:
        sent.append((message, chat_id))

    monkeypatch.setattr(pipeline_runner.notify, "send_telegram", fake_send)

    try:
        await pipeline_runner._maybe_notify(db_session, version.id, types.SimpleNamespace(status="awaiting_manazer"))
    finally:
        await registry.disconnect(version.id, ws)

    assert len(sent) == 1, "away Director with a chat_id MUST be pinged despite a null project owner"
    assert sent[0][1] == "999777"
    assert "Ownerless Proj" in sent[0][0]


@pytest.mark.asyncio
async def test_active_director_suppresses_nudge(monkeypatch) -> None:
    """A present (non-away) Director on the board → no out-of-band ping (gate unchanged)."""
    vid = uuid.uuid4()
    ws = object()
    await registry.connect(vid, ws, uuid.uuid4())  # connected, NOT away

    sent: list[tuple[str, str]] = []

    async def fake_send(message: str, chat_id: str) -> None:
        sent.append((message, chat_id))

    monkeypatch.setattr(pipeline_runner.notify, "send_telegram", fake_send)

    try:
        # db is never touched — the active-director gate returns first.
        await pipeline_runner._maybe_notify(None, vid, types.SimpleNamespace(status="awaiting_manazer"))
    finally:
        await registry.disconnect(vid, ws)

    assert sent == []


@pytest.mark.asyncio
async def test_non_settled_status_no_nudge(monkeypatch) -> None:
    """``agent_working`` is not a Director-actionable state → never nudges."""
    sent: list[tuple[str, str]] = []

    async def fake_send(message: str, chat_id: str) -> None:
        sent.append((message, chat_id))

    monkeypatch.setattr(pipeline_runner.notify, "send_telegram", fake_send)

    await pipeline_runner._maybe_notify(None, uuid.uuid4(), types.SimpleNamespace(status="agent_working"))
    assert sent == []
