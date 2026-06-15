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

# At most one in-flight dispatch per version (CR-NS-027). The event loop is single-threaded, so the
# check-and-set in ``schedule_dispatch`` is race-free. This is the concurrency fix for the incident:
# an action that left ``agent_working`` (the no-op pause) re-dispatched while a build loop was already
# running, spawning a SECOND loop — two tasks then built concurrently on the same baseline and their
# ``baseline..HEAD`` audit diffs overlapped. The guard skips the duplicate; the entry is popped when
# the task completes.
_ACTIVE_DISPATCH: dict[uuid.UUID, asyncio.Task] = {}


def schedule_dispatch(
    version_id: uuid.UUID, directive: str | None = None, *, gate_e_dispatch: str | None = None
) -> None:
    """Fire-and-forget the agent run for ``version_id`` as a tracked task.

    ``directive`` (CR-NS-018) carries the Director's framed ``return``/``ask``/
    ``answer`` message through to :func:`orchestrator.run_dispatch` so the
    re-dispatched agent acts on it instead of re-running the generic stage
    directive blind. The value is captured in-memory at schedule time (same
    process/event loop) — no DB round-trip needed.

    ``gate_e_dispatch`` selects the Gate E sub-flow: ``"designer_edit"`` (Branch B
    ``fix`` — Designer edits then continues), ``"coordinator_consult"`` (``ask`` /
    ``return`` @ gate_e — the Coordinator revises its recommendation), or ``None``.

    Single-flight per version (CR-NS-027): if a dispatch for ``version_id`` is already
    in-flight, skip this one (log + return) rather than starting a second concurrent loop.
    """
    active = _ACTIVE_DISPATCH.get(version_id)
    if active is not None and not active.done():
        logger.warning(
            "schedule_dispatch: a dispatch for version %s is already in-flight — skipping duplicate", version_id
        )
        return
    task = asyncio.create_task(_run(version_id, directive, gate_e_dispatch))
    _ACTIVE_DISPATCH[version_id] = task
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    def _clear_active(t: asyncio.Task) -> None:
        if _ACTIVE_DISPATCH.get(version_id) is t:
            del _ACTIVE_DISPATCH[version_id]

    task.add_done_callback(_clear_active)


async def _broadcast_state(version_id: uuid.UUID, state: PipelineState) -> None:
    await registry.broadcast(
        version_id,
        {"type": "state_changed", "state": PipelineStateRead.model_validate(state).model_dump(mode="json")},
    )


async def _broadcast_message(version_id: uuid.UUID, msg: PipelineMessage) -> None:
    await registry.broadcast(
        version_id,
        {"type": "message_added", "message": PipelineMessageRead.model_validate(msg).model_dump(mode="json")},
    )


def _message_callback(db, version_id: uuid.UUID):
    """Build the incremental-broadcast hook (CR-NS-018): commit + broadcast one message
    the moment it's recorded, so bubbles appear as each agent finishes — not batched at
    round end. Commit (durability boundary) THEN broadcast; refresh first so the
    DB-generated ``seq`` / ``created_at`` are populated on the instance."""

    async def _cb(msg: PipelineMessage) -> None:
        db.commit()
        db.refresh(msg)
        await _broadcast_message(version_id, msg)

    return _cb


def _activity_callback(version_id: uuid.UUID, stage: str, fallback_actor: str):
    """Build the streaming callback that broadcasts ``agent_activity`` frames.

    The frame ``actor`` is the **real** invoked role (``evt["_role"]`` tagged by
    :func:`orchestrator.invoke_agent`), so the rail steps through the agents in a
    gate_e round; falls back to the nominal stage actor. A one-shot ``active_role``
    event (no tool line) still emits a frame so a tool-less turn steps the rail."""

    async def _cb(evt: dict) -> None:
        role = (evt.get("_role") if isinstance(evt, dict) else None) or fallback_actor
        if isinstance(evt, dict) and evt.get("type") == "active_role":
            line, kind = "pracuje…", "status"
        else:
            line, kind = activity_line(evt)
        if not line:
            return
        await registry.broadcast(
            version_id,
            {"type": "agent_activity", "stage": stage, "actor": role, "kind": kind, "line": line},
        )

    return _cb


async def _run(version_id: uuid.UUID, directive: str | None = None, gate_e_dispatch: str | None = None) -> None:
    """Run one agent dispatch and broadcast the result. Owns its own session."""
    db = SessionLocal()
    try:
        pre = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
        on_event = _activity_callback(version_id, pre.current_stage, pre.current_actor) if pre else None
        # Incremental broadcast (CR-NS-018): each dispatch message is committed + streamed
        # via on_message the moment it's recorded — so the end batch is gone.
        on_message = _message_callback(db, version_id)
        try:
            state = await orchestrator.run_dispatch(
                db, version_id, on_event, directive, gate_e_dispatch=gate_e_dispatch, on_message=on_message
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — unexpected; degrade to blocked, don't hang UI.
            logger.exception("run_dispatch failed for version %s", version_id)
            db.rollback()
            # Per-message commits before the crash stay durable (incremental persistence);
            # roll back only the uncommitted tail, then always-settle to blocked.
            state, blocked_msg = _mark_blocked(db, version_id, reason=str(exc))
            db.commit()
            if blocked_msg is not None:  # crash-path message broadcast once, AFTER commit
                db.refresh(blocked_msg)  # populate DB-generated seq/created_at before serialise
                await _broadcast_message(version_id, blocked_msg)

        if state is None:
            return
        db.refresh(state)
        await _broadcast_state(version_id, state)
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


def _notify_chat_ids(db, version_id: uuid.UUID) -> list[str]:
    """Distinct Telegram recipients for the out-of-band nudge (Class J fix, CR-NS-074).

    Primary = the Director(s) who toggled **away** on an open board for this version (the registry
    knows their ``user_id``) → ping their OWN ``telegram_chat_id``. That is the whole point of the
    "Preč" toggle: get pinged when you stepped away, regardless of whether you own the project.
    Fallback = the project owner's chat_id (the original F-007 §9 recipient) for the fully-absent
    case — nobody has the board open at all.
    """
    away_ids = registry.away_director_ids(version_id)
    if away_ids:
        rows = db.execute(select(User.telegram_chat_id).where(User.id.in_(away_ids))).scalars().all()
        seen: set[str] = set()
        chat_ids: list[str] = []
        for chat in rows:
            if chat and chat not in seen:
                seen.add(chat)
                chat_ids.append(chat)
        if chat_ids:
            return chat_ids
    owner = _owner_chat_id(db, version_id)
    return [owner] if owner else []


async def _maybe_notify(db, version_id: uuid.UUID, state: PipelineState) -> None:
    """Presence-aware Telegram nudge (F-007 §9). Never blocks dispatch.

    Fires only when the pipeline settled to a Director-actionable state AND no
    Director is actively watching the board (E6: an "away" Director counts as not
    watching). Recipient = the away Director(s) on the board → project owner fallback
    (see :func:`_notify_chat_ids`).
    """
    if state.status not in _NOTIFY_STATUSES:
        return
    if registry.active_director_ids(version_id):
        # a Director is on the board AND not "away" (E6, CR-NS-038) — no out-of-band nudge. An away
        # Director (board open but stepped away) is NOT active, so the ping fires.
        return
    chat_ids = _notify_chat_ids(db, version_id)
    if not chat_ids:
        logger.info("version %s: no telegram recipient — skip notify", version_id)
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
    for chat_id in chat_ids:
        await notify.send_telegram(message, chat_id)


def _mark_blocked(
    db, version_id: uuid.UUID, reason: str | None = None
) -> tuple[PipelineState | None, PipelineMessage | None]:
    """Always-settle fallback when ``run_dispatch`` raises (CR-NS-018 robustness).

    Guarantees the board never stays ``agent_working`` after a dispatch ends — on
    any uncaught failure the state settles to ``blocked`` with a clear, recoverable
    Slovak ``next_action`` and a ``system`` message carrying the reason. The handled
    cases (claude error / parse-fail / timeout) already settle inside
    ``invoke_agent``; this catches anything else. Returns ``(state, message)`` so the
    caller can broadcast the crash-path message AFTER committing it (the incremental
    hook is bypassed here — it commits, and the except path owns its own commit)."""
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        return None, None
    detail = f": {reason[:300]}" if reason else ""
    state.status = "blocked"
    state.next_action = f"Dispatch zlyhal{detail}. Skús znova alebo vráť."
    msg = PipelineMessage(
        version_id=version_id,
        stage=state.current_stage,
        author="system",
        recipient="director",
        kind="notification",
        content=f"Agent dispatch failed{detail} — pipeline blocked.",
    )
    db.add(msg)
    db.flush()
    return state, msg
