"""DEV-152 — a build that waits on the Manažér longer than the interval reminds him on Telegram, until he acts.

Director 20.10.2026 (a project in progress must not be forgotten; NEX Manager 1.3.0 had waited on a question of the
agent since 15.10.2026): „Súhlasím“. One message at the moment a build stopped was all there was. The reminder says
the project and version, since when it waits and what it takes; the interval is in Nastavenia; it is silent for a
build the Manažér paused himself and for a fault of NEX Studio; a restart never sends it twice in an interval.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import build_reminders, system_setting
from backend.services.pipeline_ws import registry

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
LINK = "\nhttps://studio.icc.sk/riadiace-centrum"


@pytest.fixture(autouse=True)
def _link(monkeypatch):
    monkeypatch.setattr(settings, "app_public_url", "https://studio.icc.sk")
    system_setting.invalidate_cache()
    yield
    system_setting.invalidate_cache()


class Telegram:
    def __init__(self, delivers: bool = True):
        self.sent: list[tuple[str, str]] = []
        self.delivers = delivers

    async def __call__(self, message: str, chat_id: str) -> bool:
        self.sent.append((message, chat_id))
        return self.delivers


def _build(
    db,
    status="blocked",
    *,
    name="NEX Manager",
    stage="programovanie",
    block_reason="agent_question",
    since=NOW - timedelta(days=3),
):
    owner = User(
        username=f"u-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
        telegram_chat_id="chat-zoltan",
    )
    db.add(owner)
    db.flush()
    project = Project(
        name=name,
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
        owner_id=owner.id,  # the addressee of the build's Telegram messages
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.3.0")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=stage,
        current_actor="ai_agent",
        status=status,
        block_reason=block_reason if status == "blocked" else None,
        next_action="AI Agent (úloha #1.1.3) sa pýta: niečo",
    )
    db.add(state)
    db.flush()
    state.awaiting_director_since = since if status in build_reminders.WAITING else None
    db.flush()
    return state


async def test_a_build_waiting_longer_than_the_interval_reminds_once_per_interval(db_session):
    state = _build(db_session)
    telegram = Telegram()

    assert await build_reminders.remind_once(db_session, NOW, telegram) == 1
    assert telegram.sent == [
        (
            f"🔔 Pripomienka: NEX Manager 1.3.0 čaká na teba od 19.10. (3 dni) — agent sa pýta. NEX Studio{LINK}",
            "chat-zoltan",
        )
    ]
    db_session.expire_all()  # what a restarted cockpit reads — the database, not the process
    assert state.last_reminder_at == NOW

    assert await build_reminders.remind_once(db_session, NOW + timedelta(hours=1), telegram) == 0
    assert await build_reminders.remind_once(db_session, NOW + timedelta(days=1, hours=23), telegram) == 0
    assert await build_reminders.remind_once(db_session, NOW + timedelta(days=2), telegram) == 1
    assert telegram.sent[-1][0].startswith("🔔 Pripomienka: NEX Manager 1.3.0 čaká na teba od 19.10. (5 dní)")


async def test_a_build_waiting_shorter_than_the_interval_is_not_reminded_yet(db_session):
    _build(db_session, since=NOW - timedelta(days=1, hours=23))

    assert await build_reminders.remind_once(db_session, NOW, Telegram()) == 0


async def test_the_interval_is_read_from_nastavenia(db_session):
    _build(db_session, since=NOW - timedelta(days=1, hours=2))
    system_setting.upsert(db_session, build_reminders.SETTING, "1")
    system_setting.invalidate_cache()

    assert await build_reminders.remind_once(db_session, NOW, Telegram()) == 1


async def test_an_approval_says_which_phase(db_session):
    _build(db_session, status="awaiting_manazer", stage="priprava")
    telegram = Telegram()

    await build_reminders.remind_once(db_session, NOW, telegram)

    assert telegram.sent[0][0].endswith(f"— čaká na tvoje schválenie fázy Príprava. NEX Studio{LINK}")


async def test_silent_for_a_build_the_manazer_paused_and_for_a_fault_of_nex_studio(db_session):
    _build(db_session, status="paused")
    _build(db_session, name="NEX Inbox", block_reason="framework_issue")
    telegram = Telegram()

    assert await build_reminders.remind_once(db_session, NOW, telegram) == 0
    assert telegram.sent == []


async def test_a_reminder_that_reached_nobody_is_tried_again(db_session):
    state = _build(db_session)

    assert await build_reminders.remind_once(db_session, NOW, Telegram(delivers=False)) == 0
    assert state.last_reminder_at is None
    assert await build_reminders.remind_once(db_session, NOW + timedelta(hours=1), Telegram()) == 1


async def test_nobody_is_pinged_while_someone_watches_the_build(db_session, monkeypatch):
    state = _build(db_session)
    monkeypatch.setattr(
        registry, "active_director_ids", lambda vid: {uuid.uuid4()} if vid == state.version_id else set()
    )
    monkeypatch.setattr(registry, "away_director_ids", lambda vid: set())

    assert await build_reminders.remind_once(db_session, NOW, Telegram()) == 0


@pytest.mark.parametrize(("days", "words"), [(1, "1 deň"), (2, "2 dni"), (4, "4 dni"), (5, "5 dní"), (12, "12 dní")])
def test_the_days_in_slovak(days, words):
    assert build_reminders.days_words(days) == words
