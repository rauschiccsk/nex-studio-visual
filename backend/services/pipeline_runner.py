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
from backend.services.pipeline_activity import HelperTracker, activity_line
from backend.services.pipeline_ws import registry

logger = logging.getLogger(__name__)

# Settled states that warrant a system → Manažér notification (F-007 §9; CR-V2-009 status rename;
# CR-V2-017 COMMS-5). Never agent_working. The 4-phase model has three notify-worthy settles, mapping to
# the design §5.3 "away / escalation / done" notification events:
#   * ``awaiting_manazer`` — a dial-governed schvaľovací bod or the Špecifikácia approval needs the Manažér
#     (the "you're up" away nudge — the original F-007 §9 case).
#   * ``blocked``          — an agent question / error / Auditor-loop ESCALATION needs the Manažér.
#   * ``done``             — the build reached Hotovo (verified). Added in CR-V2-017 so an autonomously
#     completed build (dial=plná, no human stops) still pings an away Manažér that it is DONE — otherwise a
#     hands-off build would finish silently. (Deploy stays a separate per-customer action, D6.)
#   * ``paused``           — the spine's token-stop poistka paused the build past the token cap (STEP 1,
#     REDESIGN §9). Only the AUTOMATIC token-stop pause nudges — a Manažér's OWN ``pause`` click must not
#     ping them about their own action, so ``_maybe_notify`` further gates ``paused`` on
#     :func:`_is_token_stop_pause` (the last message being the ``token_stop`` notification).
_NOTIFY_STATUSES = ("awaiting_manazer", "blocked", "done", "paused")

# Strong refs to in-flight tasks so the event loop doesn't GC them mid-run
# (mirrors the idle/retention tasks in ``main.py``). Discarded on completion.
_BG_TASKS: set[asyncio.Task] = set()

# Backstop on the per-task relay drain (CR-V2-015): how many queued Manažér relay messages a single
# settled dispatch will drain as follow-up turns before returning (each drained relay still runs serially —
# this only bounds a pathological flood so the single-flight task always returns).
_MAX_RELAY_DRAIN = 20

# At most one in-flight dispatch per version (CR-NS-027). The event loop is single-threaded, so the
# check-and-set in ``schedule_dispatch`` is race-free. This is the concurrency fix for the incident:
# an action that left ``agent_working`` (the no-op pause) re-dispatched while a build loop was already
# running, spawning a SECOND loop — two tasks then built concurrently on the same baseline and their
# ``baseline..HEAD`` audit diffs overlapped. The guard skips the duplicate; the entry is popped when
# the task completes.
_ACTIVE_DISPATCH: dict[uuid.UUID, asyncio.Task] = {}


def schedule_dispatch(version_id: uuid.UUID, directive: str | None = None) -> None:
    """Fire-and-forget the agent run for ``version_id`` as a tracked task.

    ``directive`` (CR-NS-018) carries the Manažér's framed ``uprav``/``ask``/
    ``answer`` message through to :func:`orchestrator.run_dispatch` so the
    re-dispatched agent acts on it instead of re-running the generic phase
    directive blind. The value is captured in-memory at schedule time (same
    process/event loop) — no DB round-trip needed.

    (The v1 ``gate_e_dispatch`` sub-flow selector param was removed in CR-V2-017 — the 4-phase model has
    no Gate E; the Auditor's upfront review after Návrh replaces it.)

    Single-flight per version (CR-NS-027): if a dispatch for ``version_id`` is already
    in-flight, skip this one (log + return) rather than starting a second concurrent loop.
    """
    active = _ACTIVE_DISPATCH.get(version_id)
    if active is not None and not active.done():
        logger.warning(
            "schedule_dispatch: a dispatch for version %s is already in-flight — skipping duplicate", version_id
        )
        return
    task = asyncio.create_task(_run(version_id, directive))
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
    """Build the streaming callback that broadcasts ``agent_activity`` + ``helpers`` frames.

    The frame ``actor`` is the **real** invoked role (``evt["_role"]`` tagged by
    :func:`orchestrator.invoke_agent`), so the rail shows the live AI Agent / Auditor;
    falls back to the nominal phase actor. A one-shot ``active_role`` event (no tool
    line) still emits a frame so a tool-less turn steps the rail.

    CR-V2-018 Helpers feed: a per-dispatch :class:`HelperTracker` watches the same
    stream-json for ephemeral helper (sub-agent / ``Task``) spawn+finish and broadcasts
    a ``helpers`` frame whenever the active set changes (``count == 0`` → panel hides).
    The Auditor is **excluded from helpers** (independence): the tracker is only fed
    events whose real role is the AI Agent, so the Auditor can never register a helper
    even if a future Auditor charter were to spawn a sub-agent."""

    helpers = HelperTracker()

    async def _cb(evt: dict) -> None:
        role = (evt.get("_role") if isinstance(evt, dict) else None) or fallback_actor
        # Auditor exclusion (independence): only the AI Agent's events feed the helper tracker.
        if role == orchestrator.AI_AGENT_ROLE:
            feed = helpers.observe(evt)
            if feed is not None:
                await registry.broadcast(
                    version_id,
                    {
                        "type": "helpers",
                        "stage": stage,
                        "count": feed.count,
                        "line": feed.line,
                        "helpers": list(feed.descriptions),
                    },
                )
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


async def _run(version_id: uuid.UUID, directive: str | None = None) -> None:
    """Run one agent dispatch and broadcast the result. Owns its own session."""
    db = SessionLocal()
    try:
        pre = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
        on_event = _activity_callback(version_id, pre.current_stage, pre.current_actor) if pre else None
        # Incremental broadcast (CR-NS-018): each dispatch message is committed + streamed
        # via on_message the moment it's recorded — so the end batch is gone.
        on_message = _message_callback(db, version_id)
        try:
            # Spine STEP 1: a ``mode='conversation'`` build runs the non-phase conversation loop
            # (``run_conversation_turn``) instead of the 4-phase automaton (``run_dispatch``). The
            # conversation loop ALWAYS settles (awaiting_manazer / blocked), so there is no auto-chain to
            # run (``chain_limit`` below stays 0 by the ``agent_working`` guard); the relay-drain loop is
            # KEPT for both — it is the "message-lands-after-the-turn" rhythm, and ``drain_relay_turn`` is
            # itself mode-aware. NULL mode = the phase automaton, UNCHANGED. STEP 4
            # (step4-programovanie-design.md MD-A): a conversation build that is MID-BUILD
            # (``current_stage == 'programovanie'``, set by ``spustit_stavbu``) routes through
            # ``run_dispatch`` → ``_run_build_round`` (the EXISTING self-checking loop, reused VERBATIM —
            # routed by STAGE) instead of the conversation loop; the completion tail (MD-B) returns the stage
            # to ``priprava``, so subsequent turns route back to ``run_conversation_turn``. CR-1
            # (nex-studio-visual): a conversation build at ``current_stage == 'vizual'`` (set by
            # ``spustit_vizual``) ALSO routes through ``run_dispatch`` → ``_run_vizual_round`` — the fresh entry
            # (directive None) spins up the live preview; each relayed change-request threads a ``directive``
            # into the SAME round (the iterative "type a request → AI edits the live FE" HMR loop, spec §1).
            # Konzultácia (konzultacia-mode.md Part 1): a TERMINAL version (``current_stage == 'done'`` — a
            # hotovo-signed conversation build, a legacy schvalit-done build, or a PROD-released version)
            # answers in READ-ONLY advisory mode. Routed by the STAGE (mode-agnostic — both a conversation and
            # a legacy done build reach here), BEFORE the conversation/dispatch split. ``run_consult_turn``
            # guards ``agent_working`` (a spurious _run on a settled done version is a no-op), so it fires
            # only when a consult relay/drain armed it; it never advances a phase (returns to terminal rest).
            if pre is not None and pre.current_stage == "done":
                state = await orchestrator.run_consult_turn(db, version_id, on_event, on_message=on_message)
            elif (
                pre is not None and pre.mode == "conversation" and pre.current_stage not in ("programovanie", "vizual")
            ):
                state = await orchestrator.run_conversation_turn(
                    db, version_id, on_event, directive, on_message=on_message
                )
            else:
                state = await orchestrator.run_dispatch(db, version_id, on_event, directive, on_message=on_message)
            db.commit()
            # Engine auto-chain (CR-NS-097; v2 4-phase CR-V2-009): run_dispatch returns status=agent_working
            # ONLY when it DELIBERATELY auto-advanced the phase and wants the next phase run in THIS same
            # single-flight task — no Manažér gate between them. In the 4-phase model the chain advances
            # monotonically across STAGE_ORDER (Príprava → Návrh → Programovanie → Verifikácia) under the
            # Miera autonómie dial, plus the Auditor's bounded fix↔re-verify self-loop (Verifikácia FAIL →
            # Programovanie → Verifikácia), implemented in CR-V2-014. The Gate-E self-loop is GONE.
            # Continue dispatching with a phase-correct activity callback, broadcasting each intermediate
            # state so the board steps through the auto-advanced phases live, until it settles. Bounded as a
            # backstop by orchestrator.auto_chain_limit = len(STAGE_ORDER) + 2 * AUDITOR_LOOP_MAX (the FINAL
            # 4-phase bound — R-AUTOCHAIN finalized in CR-V2-014, so a legit 5-round Auditor loop never trips
            # it). Every real path settles (status != agent_working) well before the bound — it only stops a
            # runaway.
            guard = 0
            # Compute the backstop ONLY when we're actually about to auto-chain — avoids the read on a plain
            # settle and the version-vanished (state is None) edge.
            chain_limit = (
                orchestrator.auto_chain_limit(db, version_id)
                if state is not None and state.status == "agent_working"
                else 0
            )
            while state is not None and state.status == "agent_working" and guard < chain_limit:
                guard += 1
                await _broadcast_state(version_id, state)
                on_event = _activity_callback(version_id, state.current_stage, state.current_actor)
                state = await orchestrator.run_dispatch(db, version_id, on_event, on_message=on_message)
                db.commit()
            # Relay drain (CR-V2-015 / SPIKE-IO point (1)): a Manažér message typed into the read-only AI
            # Agent tab WHILE a turn was in flight was ENQUEUED (never a concurrent writer). Now that the
            # dispatch (incl. its auto-chain) has settled, drain queued relay(s) as the next turn(s) IN THIS
            # SAME single-flight task — so a relayed turn and the autonomous turn never invoke
            # ``invoke_claude`` concurrently on the session UUID. Bounded by the same backstop as the
            # auto-chain (each drained relay may itself auto-chain). Broadcast each settled state live.
            relay_guard = 0
            while state is not None and orchestrator.has_pending_relay(version_id) and relay_guard < _MAX_RELAY_DRAIN:
                relay_guard += 1
                await _broadcast_state(version_id, state)
                on_event = _activity_callback(version_id, state.current_stage, state.current_actor)
                state = await orchestrator.drain_relay_turn(db, version_id, on_event, on_message)
                db.commit()
                # A drained relay may have left the build auto-advancing — let it settle before the next drain.
                inner_guard = 0
                chain_limit = (
                    orchestrator.auto_chain_limit(db, version_id)
                    if state is not None and state.status == "agent_working"
                    else 0
                )
                while state is not None and state.status == "agent_working" and inner_guard < chain_limit:
                    inner_guard += 1
                    await _broadcast_state(version_id, state)
                    on_event = _activity_callback(version_id, state.current_stage, state.current_actor)
                    state = await orchestrator.run_dispatch(db, version_id, on_event, on_message=on_message)
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
        try:
            _clear_dispatch_flags(db, version_id)
        except Exception:  # noqa: BLE001 — cleanup must never mask the dispatch outcome
            logger.exception("dispatch-flag cleanup failed for version %s", version_id)
        finally:
            db.close()


def _clear_dispatch_flags(db, version_id: uuid.UUID) -> None:
    """R1-b backstop: clear the durable single-flight flag + reset the dispatch baseline on settle.

    The :class:`PipelineState.status` set listener already clears both on every ORM settle (the DRY path);
    this runner-level backstop covers a dispatch that ended WITHOUT a clean ORM status transition (e.g. the
    fast_fix auto-chain exhausting its guard while still ``agent_working``). No-op — and **no commit** — when
    neither is set, so the commit cadence is unchanged for flows that never armed them. Guarded against a
    missing :class:`PipelineState` row (version deleted mid-flight — Seam #3)."""
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None or (not state.dispatch_in_flight and not state.dispatch_baseline_sha):
        return
    state.dispatch_in_flight = False
    state.dispatch_baseline_sha = None
    db.commit()


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


def _is_token_stop_pause(db, version_id: uuid.UUID) -> bool:
    """True iff the version's MOST RECENT message is the automatic token-stop notification (spine STEP 1).

    Distinguishes the token-stop poistka pause (``_run_build_round`` wrote a ``system→manazer``
    notification with ``payload.token_stop=True`` immediately before settling ``paused``) from a Manažér's
    OWN ``pause`` click (which records NO message — ``apply_action`` just sets ``status='paused'``). Keying
    on the LATEST message means a token-stop → resume → later manual pause correctly reads as NOT a
    token-stop (the latest message is then the resumed work, not the stale token_stop note), so only the
    genuine automatic pause nudges an away Manažér. The append-only log IS the source of truth — no state
    flag to keep in sync."""
    msg = db.execute(
        select(PipelineMessage)
        .where(PipelineMessage.version_id == version_id)
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return bool(msg is not None and msg.author == "system" and (msg.payload or {}).get("token_stop") is True)


async def _maybe_notify(db, version_id: uuid.UUID, state: PipelineState) -> None:
    """Presence-aware Telegram nudge (F-007 §9). Never blocks dispatch.

    Fires only when the pipeline settled to a Director-actionable state AND no
    Director is actively watching the board (E6: an "away" Director counts as not
    watching). Recipient = the away Director(s) on the board → project owner fallback
    (see :func:`_notify_chat_ids`).
    """
    if state.status not in _NOTIFY_STATUSES:
        return
    # Spine STEP 1: a ``paused`` settle nudges ONLY for the automatic token-stop pause — a Manažér who
    # paused the build themselves just acted, so pinging them about their own pause would be spurious noise.
    if state.status == "paused" and not _is_token_stop_pause(db, version_id):
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
    # tokens like the phase id / actor). Specifics live on the board.
    proj_ver = db.execute(
        select(Project.name, Version.version_number)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id)
    ).one_or_none()
    label = f"{proj_ver[0]} {proj_ver[1]}" if proj_ver else "NEX Studio"
    # Spine STEP 1 (adversarial fix): deep-link straight to /riadiace-centrum (the spine page) instead of the
    # retired /vyvoj route (which now only redirects onward), so the out-of-band nudge lands the away Manažér
    # on the live conversation, not a bounce.
    link = f"\n{settings.app_public_url.rstrip('/')}/riadiace-centrum" if settings.app_public_url else ""
    # CR-V2-017 COMMS-5: a Hotovo (``done``) settle is a "build finished" notification, not a "you're up"
    # nudge — distinct copy so an away Manažér knows the autonomous build COMPLETED (vs needing an action).
    # Spine STEP 1: a ``paused`` settle is the token-stop poistka — distinct copy so an away Manažér knows
    # the build hit the token cap (and should check the token-limit state), not that it needs a review.
    if state.status == "done":
        message = f"✅ {label}: build hotová (overené) — NEX Studio{link}"
    elif state.status == "paused":
        message = f"⏸️ {label}: build pozastavený — prekročený token-limit — NEX Studio{link}"
    else:
        message = f"🔔 {label}: si na rade v NEX Studio{link}"
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
    # Audit P2 (2026-07-12): a crash-path settle MUST stamp its own block_reason — otherwise it INHERITS a
    # stale one (e.g. a prior 'agent_question'), and the board then renders a crashed dispatch as an
    # answerable question (offers 'answer'). A dispatch crash is a system error → recover via 'Skús znova'.
    state.block_reason = "system_error"
    state.next_action = f"Dispatch zlyhal{detail}. Skús znova alebo usmerni (Uprav)."
    msg = PipelineMessage(
        version_id=version_id,
        stage=state.current_stage,
        author="system",
        recipient="manazer",  # CR-V2-009: Director → Manažér participant rename (matches PARTICIPANT_VALUES)
        kind="notification",
        content=f"Agent dispatch failed{detail} — pipeline blocked.",
    )
    db.add(msg)
    db.flush()
    return state, msg
