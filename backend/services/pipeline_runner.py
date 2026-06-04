"""Background runner for asynchronous agent dispatch (CR-NS-018 fix-round).

``apply_action`` leaves the pipeline in ``agent_working`` and returns instantly;
the route then calls :func:`schedule_dispatch`, which runs the agent turn as a
tracked background task (single backend process) against a **fresh** session,
then broadcasts the settled state + new messages over the cockpit WS. Keeping
the agent run out of the request is the whole point — real stages take minutes
to tens of minutes (F-007 §9).

The orchestrator engine stays WS-free: broadcasting lives here (and in the
route), never in :mod:`backend.services.orchestrator`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.schemas.pipeline import PipelineMessageRead, PipelineStateRead
from backend.services import notify, orchestrator
from backend.services.pipeline_activity import activity_line
from backend.services.pipeline_ws import registry

logger = logging.getLogger(__name__)

# Settled states that warrant a Director nudge (F-007 §9). Never agent_working.
_NOTIFY_STATUSES = ("awaiting_director", "blocked")

# Strong refs to in-flight tasks so the event loop doesn't GC them mid-run
# (mirrors the idle/retention tasks in ``main.py``). Discarded on completion.
_BG_TASKS: set[asyncio.Task] = set()


def schedule_dispatch(version_id: uuid.UUID) -> None:
    """Fire-and-forget the agent run for ``version_id`` as a tracked task."""
    task = asyncio.create_task(_run(version_id))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def _message_ids(db, version_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.execute(select(PipelineMessage.id).where(PipelineMessage.version_id == version_id)).scalars())


async def _broadcast_state(version_id: uuid.UUID, state: PipelineState) -> None:
    await registry.broadcast(
        version_id,
        {"type": "state_changed", "state": PipelineStateRead.model_validate(state).model_dump(mode="json")},
    )


async def _broadcast_new_messages(db, version_id: uuid.UUID, pre_ids: set[uuid.UUID]) -> None:
    new_msgs = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id)
            .order_by(PipelineMessage.created_at.asc())
        )
        .scalars()
        .all()
    )
    for m in new_msgs:
        if m.id in pre_ids:
            continue
        await registry.broadcast(
            version_id,
            {"type": "message_added", "message": PipelineMessageRead.model_validate(m).model_dump(mode="json")},
        )


def _activity_callback(version_id: uuid.UUID, stage: str, actor: str):
    """Build the streaming callback that broadcasts ``agent_activity`` frames."""

    async def _cb(evt: dict) -> None:
        line, kind = activity_line(evt)
        if not line:
            return
        await registry.broadcast(
            version_id,
            {"type": "agent_activity", "stage": stage, "actor": actor, "kind": kind, "line": line},
        )

    return _cb


async def _run(version_id: uuid.UUID) -> None:
    """Run one agent dispatch and broadcast the result. Owns its own session."""
    db = SessionLocal()
    try:
        pre_ids = _message_ids(db, version_id)
        pre = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
        on_event = _activity_callback(version_id, pre.current_stage, pre.current_actor) if pre else None
        try:
            state = await orchestrator.run_dispatch(db, version_id, on_event)
            db.commit()
        except Exception:  # noqa: BLE001 — unexpected; degrade to blocked, don't hang UI.
            logger.exception("run_dispatch failed for version %s", version_id)
            db.rollback()
            state = _mark_blocked(db, version_id)
            db.commit()

        if state is None:
            return
        db.refresh(state)
        await _broadcast_state(version_id, state)
        await _broadcast_new_messages(db, version_id, pre_ids)
        await _maybe_notify(db, version_id, state)
    finally:
        db.close()


def _owner_chat_id(db, version_id: uuid.UUID) -> str | None:
    """Telegram chat_id of the version's project owner, or ``None``."""
    return db.execute(
        select(User.telegram_chat_id)
        .join(Project, Project.owner_id == User.id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id)
    ).scalar_one_or_none()


async def _maybe_notify(db, version_id: uuid.UUID, state: PipelineState) -> None:
    """Presence-aware Telegram nudge (F-007 §9). Never blocks dispatch.

    Fires only when the pipeline settled to a Director-actionable state AND no
    Director has a live board WS for this version (presence = they'd already see
    it). Recipient = version → project owner → ``telegram_chat_id``.
    """
    if state.status not in _NOTIFY_STATUSES:
        return
    if registry.present_director_ids(version_id):
        return  # a Director is already on the board — no out-of-band nudge
    chat_id = _owner_chat_id(db, version_id)
    if not chat_id:
        logger.info("version %s: no owner telegram_chat_id — skip notify", version_id)
        return
    # Generic nudge only — NEVER embed the raw next_action (it carries machine
    # tokens like 'coordinator'/'gate_a'). Specifics live on the board.
    proj_ver = db.execute(
        select(Project.name, Version.version_number)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id)
    ).one_or_none()
    label = f"{proj_ver[0]} {proj_ver[1]}" if proj_ver else "NEX Studio"
    link = f"\n{settings.app_public_url.rstrip('/')}/cockpit" if settings.app_public_url else ""
    message = f"🔔 {label}: si na rade v NEX Studio cockpite{link}"
    await notify.send_telegram(message, chat_id)


def _mark_blocked(db, version_id: uuid.UUID) -> PipelineState | None:
    """Fallback when ``run_dispatch`` raises unexpectedly: block + notify.

    The handled cases (claude error / parse-fail / timeout) already settle to
    ``blocked`` inside ``invoke_agent``; this only catches truly unexpected
    failures so the UI never stays stuck on ``agent_working``.
    """
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        return None
    state.status = "blocked"
    state.next_action = "Blokované: neočakávaná chyba pri behu agenta. Eskalované Directorovi."
    db.add(
        PipelineMessage(
            version_id=version_id,
            stage=state.current_stage,
            author="system",
            recipient="director",
            kind="notification",
            content="Agent dispatch failed unexpectedly — pipeline blocked.",
        )
    )
    db.flush()
    return state
