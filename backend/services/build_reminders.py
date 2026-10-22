"""A build that waits on the Manažér longer than the interval reminds him on Telegram — and again, until someone acts.

Director 20.10.2026 (a project in progress must not be forgotten; NEX Manager 1.3.0 had waited on a question of the
agent since 15.10.2026): „Súhlasím“ to the plan — DEV-152. The cockpit sent ONE message at the moment a build stopped
on a person (:func:`backend.services.pipeline_runner._maybe_notify`); nothing after it.

* Only a build waiting on the Manažér — at a schvaľovací bod, the Špecifikácia or a question of the agent. Not one
  the Manažér paused himself (his decision; „Kde sme skončili“ says why), not one stopped on a fault of NEX Studio
  (the technical team's, not his).
* The interval is in Nastavenia (``build_reminder_after_days``), not in the code.
* The same recipients as the message of the moment (``_notify_chat_ids``); nobody is pinged while someone watches the
  build and nobody stepped away — they see it.
* When the last reminder went is in the database (``pipeline_state.last_reminder_at``) — a restart of the cockpit
  never sends one twice in an interval; a reminder that reached nobody is tried again on the next pass.
* The sentence says the project and version, since when it waits and what it takes, in words — no machine codes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.local_time import local_time
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import notify, system_setting
from backend.services.pipeline_runner import _cockpit_link, _notify_chat_ids
from backend.services.pipeline_ws import registry

logger = logging.getLogger(__name__)

SETTING = "build_reminder_after_days"
#: A build waiting on the Manažér.
WAITING = ("awaiting_manazer", "blocked")
#: Stopped on a fault of NEX Studio — the technical team's to fix, not the Manažér's to act on.
NOT_THE_MANAZERS = ("framework_issue",)

_PHASE = {
    "priprava": "Príprava",
    "navrh": "Návrh",
    "vizual": "Vizuál",
    "programovanie": "Programovanie",
    "verifikacia": "Verifikácia",
}
_BLOCKED = {
    "agent_question": "agent sa pýta",
    "decision_needed": "treba tvoje rozhodnutie",
    "schema_approval": "treba schváliť štruktúru databázy",
    "agent_error": "agent zlyhal — treba skúsiť znova alebo upraviť zadanie",
    "system_error": "stavba spadla na systémovej chybe — treba skúsiť znova",
    "parse_exhaustion": "agent neodovzdal čitateľnú správu — treba skúsiť znova",
    "check_failed": "kontrola neprešla — treba rozhodnúť, ako ďalej",
}

Sender = Callable[[str, str], Awaitable[bool]]


def days_words(days: int) -> str:
    """„1 deň“, „3 dni“, „5 dní“."""
    if days == 1:
        return "1 deň"
    return f"{days} dni" if 2 <= days <= 4 else f"{days} dní"


def what_it_takes(state: PipelineState) -> str:
    """What the Manažér has to do, in words — never the agent's text, never a code."""
    if state.status == "blocked":
        return _BLOCKED.get(state.block_reason or "", "stavba stojí — treba sa pozrieť do Riadiaceho centra")
    return f"čaká na tvoje schválenie fázy {_PHASE.get(state.current_stage, state.current_stage)}"


def is_due(state: PipelineState, now: datetime, after: timedelta) -> bool:
    """Waiting on the Manažér longer than ``after``, and no reminder of this waiting within ``after``."""
    since = state.awaiting_director_since
    if state.status not in WAITING or state.block_reason in NOT_THE_MANAZERS or since is None:
        return False
    if now - since < after:
        return False
    # A reminder of an earlier waiting is older than this one's start, hence older than ``after`` here — one rule does.
    last = state.last_reminder_at
    return last is None or now - last >= after


def reminder_text(db: Session, state: PipelineState, now: datetime) -> str:
    project, number = db.execute(
        select(Project.name, Version.version_number)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == state.version_id)
    ).one()
    since = state.awaiting_director_since
    days = (now - since).days
    return (
        f"🔔 Pripomienka: {project} {number} čaká na teba od {local_time(since):%d.%m.} ({days_words(days)}) — "
        f"{what_it_takes(state)}. NEX Studio{_cockpit_link()}"
    )


async def remind_once(db: Session, now: Optional[datetime] = None, send: Sender = notify.send_telegram) -> int:
    """One pass: every build due for a reminder gets one; returns how many were delivered."""
    now = now or datetime.now(timezone.utc)
    after = timedelta(days=max(1, system_setting.get_int(db, SETTING)))
    states = db.execute(
        select(PipelineState).where(
            PipelineState.status.in_(WAITING), PipelineState.awaiting_director_since.is_not(None)
        )
    ).scalars()
    delivered = 0
    for state in list(states):
        if not is_due(state, now, after):
            continue
        if registry.active_director_ids(state.version_id) and not registry.away_director_ids(state.version_id):
            continue  # someone has the build open and nobody stepped away — they see it there
        chat_ids = _notify_chat_ids(db, state.version_id)
        if not chat_ids:
            logger.info("version %s: no telegram recipient for the reminder", state.version_id)
            continue
        message = reminder_text(db, state, now)
        reached = [chat for chat in chat_ids if await send(message, chat)]
        if reached:
            state.last_reminder_at = now
            db.commit()
            delivered += 1
        else:
            logger.warning("version %s: the reminder reached nobody — tried again next pass", state.version_id)
    return delivered
