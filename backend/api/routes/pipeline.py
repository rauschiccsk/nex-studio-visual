"""REST + WebSocket router for the Orchestration Cockpit (F-007 §6, CR-NS-018 Phase 3).

* ``GET    /pipeline/{version_id}``          → board (state + last N messages)
* ``GET    /pipeline/{version_id}/messages`` → paginated message log
* ``POST   /pipeline/{version_id}/action``   → Director action → orchestrator,
  then broadcasts ``state_changed`` + ``message_added`` to live board sockets.
* ``WS     /pipeline/ws/{version_id}?token`` → live board feed + §9 presence.

All Director-only (``require_ri_role`` / ``verify_ws_token`` + ``role == 'ri'``).
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.security import require_ri_role, verify_ws_token
from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal, get_db
from backend.schemas.agent_terminal import AgentTerminalSessionRead
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.pipeline import (
    AgentSession,
    BoardTask,
    ChangeRequestCaptureRequest,
    ChangeRequestCaptureResponse,
    FastFixStartRequest,
    FastFixStartResponse,
    PipelineActionRequest,
    PipelineBoardRead,
    PipelineMessageRead,
    PipelineRelayRequest,
    PipelineRelayResponse,
    PipelineStateRead,
)
from backend.services import agent_terminal as agent_terminal_service
from backend.services import change_request as change_request_service
from backend.services import fast_fix as fast_fix_service
from backend.services import orchestrator, pipeline_runner
from backend.services.agent_terminal import AgentTerminalError, SessionConflictError
from backend.services.orchestrator import OrchestratorError
from backend.services.pipeline_ws import registry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Pipeline"])

_DEFAULT_RECENT = 50


def _version_exists(db: Session, version_id: uuid.UUID) -> bool:
    return db.execute(select(Version.id).where(Version.id == version_id)).scalar_one_or_none() is not None


def _recent_messages(db: Session, version_id: uuid.UUID, limit: int) -> list[PipelineMessage]:
    rows = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id)
            .order_by(PipelineMessage.seq.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))  # display oldest→newest


def _board(db: Session, version_id: uuid.UUID, limit: int = _DEFAULT_RECENT) -> PipelineBoardRead:
    """Assemble the Vývoj board snapshot (CR-V2-021) — the 4-phase model.

    The horizontal phase bar + per-phase artifacts are derived FE-side from ``state.current_stage`` (the
    build-position ``●``) + ``recent_messages`` (each phase's durable Špecifikácia / design doc / coding log
    / Auditor verdict, carried in the gate_report / verdict ``payload['report']``). The route supplies only:
    the offerable dial-governed actions, the Programovanie split-view task progress (build-readiness +
    current task), and the two-agent who's-up liveness. The v1 Gate-E / gate_g / Coordinator board fields
    are gone (no Gate E, no release-gate, no Coordinator hub in the 4-phase model)."""
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    # WS-C1 (CR-NS-030): build-readiness facts for the FE to disable the Programovanie sign-off button when
    # not satisfiable (the state-only available_actions can't see todos / open findings) + drive the
    # Programovanie split-view task progress (design §4.5).
    all_tasks_done, build_open_findings = (
        orchestrator.build_readiness(db, version_id) if state is not None else (True, 0)
    )
    # WS-C2 (CR-NS-035): the task in focus for the who's-up status (only during the Programovanie phase).
    ct = (
        orchestrator.current_build_task(db, version_id)
        if (state is not None and state.current_stage == "programovanie")
        else None
    )
    # Per-agent liveness for the who's-up status — a bounded one-query scan over the two v2 agent sessions.
    sessions = orchestrator.agent_sessions(db, version_id, state) if state is not None else []
    # WS-C1 (CR-NS-030): backend-authoritative offerable actions (dial-governed v2 verbs) so the FE can't
    # show no-op buttons. CR-V2-037: drop the dial-governed "Schváliť" out of Návrh while the task plan is
    # still EMPTY (a per-feat pass crashed past its retries → 0 tasks) — advancing then would build nothing.
    # apply_action enforces the same rule authoritatively; this just hides the dead button (the state-only
    # determine_available_actions can't see the DB-derived plan presence).
    available_actions: list[str] = sorted(orchestrator.determine_available_actions(state)) if state is not None else []
    # Durable spec-approval signal (STEP 2 follow-up) — the SINGLE shared probe (orchestrator.spec_approved,
    # step3-plan-design.md FIX2): drives both the FE Špecifikácia badge below AND the STEP-3 zostav_plan
    # post-filter here. One indexed exists query; correct for conversation + legacy builds alike.
    spec_approved = orchestrator.spec_approved(db, version_id)
    if (
        state is not None
        and state.current_stage == "navrh"
        and "schvalit" in available_actions
        and not orchestrator.navrh_plan_materialized(db, version_id)
    ):
        available_actions = [a for a in available_actions if a != "schvalit"]
    # STEP 3 (step3-plan-design.md FIX2): the state-only ``determine_available_actions`` offers ``zostav_plan``
    # unconditionally at ``priprava``; POST-FILTER it here (mirror of the schvalit filter above) — drop it
    # unless this is a conversation build whose Špecifikácia is approved and whose plan is not yet
    # materialized. ``apply_action`` enforces the same rule authoritatively; this hides the dead button.
    if (
        state is not None
        and "zostav_plan" in available_actions
        and not (
            state.mode == "conversation" and spec_approved and not orchestrator.navrh_plan_materialized(db, version_id)
        )
    ):
        available_actions = [a for a in available_actions if a != "zostav_plan"]
    # STEP 4 (step4-programovanie-design.md MD-A): POST-FILTER ``spustit_stavbu`` (mirror of the zostav_plan
    # filter above) — the state-only ``determine_available_actions`` offers it unconditionally at ``priprava``;
    # drop it here unless this is a conversation build whose Špecifikácia is approved, whose task plan is
    # MATERIALIZED, and whose build has NOT yet started. ``apply_action`` enforces the same rule
    # authoritatively; this hides the dead button.
    if (
        state is not None
        and "spustit_stavbu" in available_actions
        and not (
            state.mode == "conversation"
            and spec_approved
            and orchestrator.navrh_plan_materialized(db, version_id)
            and not orchestrator._build_started(db, version_id)
        )
    ):
        available_actions = [a for a in available_actions if a != "spustit_stavbu"]
    # STEP 5 (step5-kontrola-design.md K-1): POST-FILTER ``skontrolovat`` (mirror of the spustit_stavbu filter
    # above) — the state-only ``determine_available_actions`` offers it unconditionally at ``priprava``; drop it
    # here unless this is a conversation build whose Špecifikácia is approved, whose Programovanie has COMPLETED,
    # and whose latest completed build has NOT yet been checked (K-4). ``apply_action`` enforces the same rule
    # authoritatively; this hides the dead button. Reuses the ``spec_approved`` local computed above.
    if (
        state is not None
        and "skontrolovat" in available_actions
        and not (
            state.mode == "conversation"
            and spec_approved
            and orchestrator.programming_complete(db, version_id)
            and not orchestrator.kontrola_done(db, version_id)
        )
    ):
        available_actions = [a for a in available_actions if a != "skontrolovat"]
    # STEP 6 (step6-hotovo-design.md MD-1): POST-FILTER ``hotovo`` (mirror of the skontrolovat filter above) —
    # the state-only ``determine_available_actions`` offers it unconditionally at ``priprava``; drop it here
    # unless this is a conversation build whose Špecifikácia is approved, whose Kontrola has run for the latest
    # build, and which is NOT already ``done`` (the terminal Hotovo state itself blocks a re-sign — MD-2).
    # ``apply_action`` enforces the same rule authoritatively; this hides the dead button. Reuses ``spec_approved``.
    if (
        state is not None
        and "hotovo" in available_actions
        and not (
            state.mode == "conversation"
            and spec_approved
            # K-3: gate on kontrola_PASSED (ran AND runtime floor not red), not the pass-blind kontrola_done —
            # a red-floor Kontrola must not leave "Označiť ako hotové" enabled next to the red note.
            and orchestrator.kontrola_passed(db, version_id)
            and state.current_stage != "done"
        )
    ):
        available_actions = [a for a in available_actions if a != "hotovo"]
    # Bug 1 (cockpit-timeout-and-activity-fix.md): the state-only ``determine_available_actions`` ALWAYS
    # offers ``schvalit`` at a SETTLED Programovanie — but ``schvalit`` ADVANCES programovanie → verifikacia
    # (FINISH). After a build TIMEOUT settles the round ``awaiting_manazer`` ("review & continue") with tasks
    # still REMAINING, that is a footgun: it would FINISH a half-built version, and there is no clean
    # "Pokračovať v stavbe". Gate ``schvalit`` vs ``pokracovat`` on the DB-derived tasks-remaining signal
    # (``all_tasks_done`` from ``build_readiness`` above — the SAME probe, no extra query): tasks REMAIN →
    # DROP ``schvalit`` and OFFER ``pokracovat`` (resume the build loop, ``apply_action`` re-dispatches
    # ``_run_build_round`` from awaiting_manazer); ``all_tasks_done`` → keep ``schvalit`` (advance to
    # Verifikácia), as today. Placed BEFORE the conversation-mode ``schvalit`` drop so a conversation build's
    # timeout ALSO gets a clean "Pokračovať" (its ``schvalit`` is dropped below either way). ``uprav`` / ``ask``
    # (and ``answer`` on a blocked settle) stay in both cases.
    if (
        state is not None
        and state.current_stage == "programovanie"
        and "schvalit" in available_actions
        and not all_tasks_done
    ):
        remaining = [a for a in available_actions if a != "schvalit"]
        if "pokracovat" not in remaining:
            remaining.append("pokracovat")
        available_actions = sorted(remaining)  # keep the sorted invariant of the initial computation
    # STEP 4 (step4-programovanie-design.md MAJOR): a conversation build NEVER walks the phase automaton (its
    # Programovanie returns to the rozhovor; kontrola is STEP 5), so the legacy phase-gate verb ``schvalit``
    # (and, defensively, the Auditor ``verdict``) must never be OFFERED on it — DROP them when
    # mode=='conversation' (two-layer belt with the ``apply_action('schvalit')`` raise, mirroring zostav_plan).
    # A legacy (mode NULL) build is UNTOUCHED.
    if state is not None and state.mode == "conversation":
        available_actions = [a for a in available_actions if a not in ("schvalit", "verdict")]
    # STEP 4 (step4-programovanie-design.md MINOR): once a conversation build's Špecifikácia is approved it is
    # FROZEN (STEP 2), so ``approve_spec`` is spent — DROP it so the settled-priprava board never re-offers a
    # phantom "Schváliť špecifikáciu". Still offered PRE-approval (the real end-Príprava stop). Mirrors the
    # zostav_plan post-filter and cleans a pre-existing latent re-offer on the conversation register.
    if state is not None and "approve_spec" in available_actions and state.mode == "conversation" and spec_approved:
        available_actions = [a for a in available_actions if a != "approve_spec"]
    # CR-V2-056 (reality-anchoring): compute "verified" LIVE from the repo (PASS-bound SHA vs current HEAD) so
    # the board never shows a frozen PASS — a version whose HEAD drifted past its verified commit reads
    # 'sha_drift' and the FE flags it. One HEAD read for this single-version view.
    verified, verified_provenance = (
        orchestrator.version_verified(db, version_id) if state is not None else (False, "no_pass")
    )
    # CR-V2-057 (drift re-verify): OFFER ``overit_znovu`` when the live provenance is 'sha_drift' — the version
    # WAS verified (PASS/Hotovo) but HEAD moved past the verified commit, so the green "overená" is stale. The
    # handler (apply_action) guards on EXACTLY this (settled state + sha_drift); surface the button here so the
    # Manažér can re-run the Auditor against current HEAD in ONE click, instead of the heavier Upraviť fix-loop.
    # Without this the fully-built handler was unreachable — never offered by determine_available_actions (which
    # is state-only and can't do the repo HEAD read) nor anywhere else. Honest-by-construction: appended ONLY
    # when actually drifted AND settled (done/awaiting_manazer), so the FE bar is gated by available_actions like
    # every other verb. Note a done/drifted version otherwise has an EMPTY action set — this is its only action.
    if (
        state is not None
        and verified_provenance == "sha_drift"
        and state.status in ("done", "awaiting_manazer")
        and "overit_znovu" not in available_actions
    ):
        available_actions = sorted([*available_actions, "overit_znovu"])
    return PipelineBoardRead(
        state=PipelineStateRead.model_validate(state) if state is not None else None,
        recent_messages=[PipelineMessageRead.model_validate(m) for m in _recent_messages(db, version_id, limit)],
        available_actions=available_actions,
        all_tasks_done=all_tasks_done,
        build_open_findings=build_open_findings,
        current_task=BoardTask(number=ct.number, title=ct.title) if ct is not None else None,
        agent_sessions=[AgentSession(**s) for s in sessions],
        verified=verified,
        verified_provenance=verified_provenance,
        spec_approved=spec_approved,
    )


def _map_orch_error(exc: OrchestratorError) -> HTTPException:
    msg = str(exc)
    lowered = msg.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    if "already started" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


@router.post("/fast-fix", response_model=FastFixStartResponse, status_code=status.HTTP_201_CREATED)
async def start_fast_fix(
    payload: FastFixStartRequest,
    _current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> FastFixStartResponse:
    """Fast-Fix Lane entry (F-009, CR-NS-094; v2 short path CR-V2-028) — the "Rýchla oprava" one-prompt action.

    Auto-creates the next PATCH version (``vX.Y.Z+1`` from the project's semver max) and starts a
    ``fast_fix`` pipeline carrying the Manažér's directive (which IS the brief). The lane runs the v2 SHORT
    path autonomously to the verified boundary — lightweight Príprava (no spec dialogue) → AI Agent
    self-checking Programovanie → a LIGHT focused Auditor Verifikácia (fix works + no regression) → Hotovo
    — with ZERO mid-flight approvals by default. It STOPS at verified; it does NOT auto-deploy (OQ-3/D6 —
    deploy is the normal manual per-customer Nasadiť in the UAT/PROD tabs, CR-V2-027). Declared before the
    ``/{version_id}`` routes so ``fast-fix`` is never parsed as a version id.
    """
    if db.execute(select(Project.id).where(Project.id == payload.project_id)).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    pre_count = db.execute(
        select(func.count()).select_from(Version).where(Version.project_id == payload.project_id)
    ).scalar_one()
    try:
        version = fast_fix_service.create_patch_version(db, project_id=payload.project_id, user_id=_current_user.id)
        state = await orchestrator.apply_action(
            db,
            version_id=version.id,
            action="start",
            payload={"flow_type": "fast_fix", "directive": payload.directive},
        )
    except OrchestratorError as exc:
        db.rollback()
        raise _map_orch_error(exc) from exc
    except ValueError as exc:
        db.rollback()
        # No semver base version / bumped collision — a client/data precondition, not a server fault.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    version_id = version.id
    db.commit()
    db.refresh(state)

    # Stream the fresh board to any already-open sockets (none yet for a brand-new version, but
    # symmetric with post_action and harmless). The kickoff Coordinator runs in the background.
    await registry.broadcast(
        version_id,
        {"type": "state_changed", "state": PipelineStateRead.model_validate(state).model_dump(mode="json")},
    )
    if state.status == "agent_working":
        pipeline_runner.schedule_dispatch(version_id, None)

    logger.info("Fast-Fix started: version %s (project had %d versions before)", version_id, pre_count)
    return FastFixStartResponse(version_id=version_id, board=_board(db, version_id))


@router.get("/{version_id}", response_model=PipelineBoardRead)
def get_board(
    version_id: uuid.UUID,
    limit: int = Query(default=_DEFAULT_RECENT, ge=1, le=200),
    _current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> PipelineBoardRead:
    """Return the board snapshot. ``state`` is ``None`` until the pipeline starts."""
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return _board(db, version_id, limit)


@router.get("/{version_id}/messages", response_model=PaginatedResponse[PipelineMessageRead])
def list_messages(
    version_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> PaginatedResponse[PipelineMessageRead]:
    """Paginated message log (oldest→newest)."""
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    total = db.execute(
        select(func.count()).select_from(PipelineMessage).where(PipelineMessage.version_id == version_id)
    ).scalar_one()
    rows = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id)
            .order_by(PipelineMessage.seq.asc())
            .offset(skip)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return PaginatedResponse[PipelineMessageRead](
        items=[PipelineMessageRead.model_validate(m) for m in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/{version_id}/action", response_model=PipelineBoardRead)
async def post_action(
    version_id: uuid.UUID,
    payload: PipelineActionRequest,
    _current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> PipelineBoardRead:
    """Apply a Director action; broadcast the resulting state + new messages."""
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    pre_ids = {
        row for row in db.execute(select(PipelineMessage.id).where(PipelineMessage.version_id == version_id)).scalars()
    }
    try:
        state = await orchestrator.apply_action(
            db, version_id=version_id, action=payload.action, payload=payload.payload
        )
    except OrchestratorError as exc:
        db.rollback()
        raise _map_orch_error(exc) from exc
    db.commit()
    db.refresh(state)

    new_msgs = [m for m in _recent_messages(db, version_id, 200) if m.id not in pre_ids]
    await registry.broadcast(
        version_id,
        {"type": "state_changed", "state": PipelineStateRead.model_validate(state).model_dump(mode="json")},
    )
    for m in new_msgs:
        await registry.broadcast(
            version_id,
            {"type": "message_added", "message": PipelineMessageRead.model_validate(m).model_dump(mode="json")},
        )

    # Async dispatch (CR-NS-018 fix-round): the action left an agent working —
    # run it in the background; its result lands later via WS. POST returns now.
    # The Manažér's uprav/ask/answer content is threaded into the re-dispatch prompt so
    # the agent acts on it (else it re-runs blind on the generic directive);
    # fresh-phase dispatch (start/approve/verdict) → directive None.
    # (The v1 Gate-E sub-flow selector was removed in CR-V2-017 — the 4-phase model has no Gate E;
    # the Auditor's upfront review after Návrh replaces it.)
    if state.status == "agent_working":
        directive = orchestrator.dispatch_directive(
            db, version_id, payload.action, payload.payload or {}, state.current_stage
        )
        pipeline_runner.schedule_dispatch(version_id, directive)

    return _board(db, version_id)


@router.post("/{version_id}/relay", response_model=PipelineRelayResponse)
async def post_relay(
    version_id: uuid.UUID,
    payload: PipelineRelayRequest,
    _current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> PipelineRelayResponse:
    """Relay a Manažér message to the AI Agent as the engine's next turn (CR-V2-015 / SPIKE-IO Model B).

    This is the canonical Manažér→AI-Agent channel for the read-only AI Agent tab: the message is RELAYED
    by the engine (the sole writer to the warm ``claude`` session) as the next ``--resume`` turn — it is
    NEVER keystroked into the PTY (no concurrent second writer). When a turn is in flight the message is
    enqueued behind it (``deferred=True``) and the in-flight dispatch drains it next; when the build is
    settled it dispatches immediately as an ``ask``/``answer`` turn and we schedule the background run."""
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    pre_ids = {
        row for row in db.execute(select(PipelineMessage.id).where(PipelineMessage.version_id == version_id)).scalars()
    }
    try:
        result = await orchestrator.relay_manazer_message(db, version_id=version_id, text=payload.text)
    except OrchestratorError as exc:
        db.rollback()
        raise _map_orch_error(exc) from exc
    db.commit()
    db.refresh(result.state)

    new_msgs = [m for m in _recent_messages(db, version_id, 200) if m.id not in pre_ids]
    await registry.broadcast(
        version_id,
        {"type": "state_changed", "state": PipelineStateRead.model_validate(result.state).model_dump(mode="json")},
    )
    for m in new_msgs:
        await registry.broadcast(
            version_id,
            {"type": "message_added", "message": PipelineMessageRead.model_validate(m).model_dump(mode="json")},
        )

    # Dispatched-now (settled build) → run the relay turn in the background, exactly like ``post_action``.
    # Deferred (in-flight) → the running dispatch drains the queue itself; do NOT schedule a second loop.
    if not result.deferred and result.state.status == "agent_working":
        directive = orchestrator.dispatch_directive(
            db, version_id, result.action or "ask", {"text": payload.text}, result.state.current_stage
        )
        pipeline_runner.schedule_dispatch(version_id, directive)

    return PipelineRelayResponse(deferred=result.deferred, board=_board(db, version_id))


@router.post("/{version_id}/change-request", response_model=ChangeRequestCaptureResponse)
def post_change_request(
    version_id: uuid.UUID,
    payload: ChangeRequestCaptureRequest,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> ChangeRequestCaptureResponse:
    """Capture a read-only consult's change request → a NEW draft version (konzultacia-mode.md Part 2; Fix 3/4).

    ``version_id`` (path) is the FINISHED version the Konzultácia ran on; ``payload.message_id`` is the SOURCE
    consult message that carried the ``change_request`` marker. Its project owns the new backlog ``REQ-N`` and
    the minted next version (DRAFT / ``planned``, NO ``PipelineState``, NO build running). NEVER starts a build
    — the Manažér opens the new version and engages deliberately. Idempotent per source message: a repeat call
    returns the EXISTING minted version. Returns ``project_slug`` + version id/number so the FE navigates using
    the RETURNED slug (Fix 4)."""
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    # Defense: the source message must belong to the consulted version in the path (a mismatched id is a 404).
    msg_version_id = db.execute(
        select(PipelineMessage.version_id).where(PipelineMessage.id == payload.message_id)
    ).scalar_one_or_none()
    if msg_version_id is None or msg_version_id != version_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consult message not found for this version")
    try:
        result = change_request_service.capture(
            db,
            source_message_id=payload.message_id,
            user_id=current_user.id,
        )
    except ValueError as exc:
        db.rollback()
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail) from exc
    db.commit()
    return ChangeRequestCaptureResponse(
        version_id=result.version_id,
        version_number=result.version_number,
        project_slug=result.project_slug,
        backlog_number=result.backlog_number,
    )


@router.post("/{version_id}/debug-terminal", response_model=AgentTerminalSessionRead)
async def open_debug_terminal(
    version_id: uuid.UUID,
    role: str = Query(..., description="orchestrator agent role to attach to"),
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> AgentTerminalSessionRead:
    """Break-glass: attach an interactive Manažér terminal to the headless agent session (CR-V2-015).

    Resumes the existing ``orchestrator_session.claude_session_id`` for ``(project, role)`` into a
    Manažér-owned ``agent_terminal_sessions`` row so the standard AgentTerminal WS can stream it. This is an
    OUT-OF-BAND human break-glass ONLY — the first-class Manažér↔AI-Agent channel is the read-only tab +
    the engine relay (:func:`post_relay`). To preserve the single-writer invariant (SPIKE-IO Model B), the
    debug-attach PTY is **gated so it cannot open while the engine is driving the session** (an open
    write-capable PTY mid-turn would be a second concurrent writer). When the engine IS driving, this
    returns 409; otherwise it attaches (and ``write_input`` is still per-keystroke-guarded as a backstop).
    """
    if not _version_exists(db, version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    # Debug-attach accepts the orchestrator roles (CR-V2-007: ai-agent / auditor) — NOT just the
    # spawn-API's AI-Agent-only set. Validate up front so a bad role is a clean 422, not a misleading 404.
    try:
        agent_terminal_service._validate_debug_attach_role(role)
    except AgentTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    slug = orchestrator._project_slug_for_version(db, version_id)
    # CR-V2-007: the debug-attach param is a charter-path slug (hyphen, ``ai-agent``); the
    # OrchestratorSession.role column holds the DB value (underscore, ``ai_agent``). Bridge them via the
    # single orchestrator mapper so the two spellings never diverge.
    db_role = orchestrator.db_role_for_charter_slug(role)
    orch = db.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == slug,
            OrchestratorSession.role == db_role,
        )
    ).scalar_one_or_none()
    if orch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No orchestrator session for role '{role}' in project '{slug}'",
        )

    # CR-V2-015 single-writer gate (SPIKE-IO Model B): refuse to open a write-capable break-glass PTY while
    # the engine is currently driving this ``claude_session_id`` (an active ``invoke_claude`` turn). Opening
    # one mid-turn would create a second concurrent writer that corrupts session memory. The first-class
    # channel during an active turn is the engine relay (POST /relay), not a raw PTY.
    if orchestrator.is_session_engine_busy(orch.claude_session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Engine is driving this session — debug-attach refused mid-turn. "
                "Use the AI Agent relay to message it, or attach when the build is idle."
            ),
        )

    try:
        row = await agent_terminal_service.spawn(
            user_id=current_user.id,
            role=role,
            project_slug=slug,
            db=db,
            claude_session_id=orch.claude_session_id,
        )
    except SessionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AgentTerminalSessionRead.model_validate(row)


async def _apply_ws_presence_frame(version_id: uuid.UUID, websocket: WebSocket, raw: str) -> None:
    """Act on an inbound board-WS frame (E6, CR-NS-038). The ONLY actionable frame is the presence
    annotation ``{"type":"presence","away":<bool>}`` → :meth:`registry.set_away`; any other or
    malformed frame is ignored SILENTLY. Never raises — the caller's loop must keep draining so a
    real ``WebSocketDisconnect`` still surfaces."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return  # non-JSON frame — ignore
    # A presence frame is acted on ONLY when well-formed: type "presence" + an explicit BOOL `away`.
    # A frame missing/with a non-bool `away` is malformed → ignored (don't coerce None→False, which
    # would silently clear "away" off a bad frame).
    if isinstance(msg, dict) and msg.get("type") == "presence" and isinstance(msg.get("away"), bool):
        await registry.set_away(version_id, websocket, msg["away"])


@router.websocket("/ws/{version_id}")
async def pipeline_ws(
    websocket: WebSocket,
    version_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    """Live board feed for a version. The connection doubles as the §9 presence."""
    db = SessionLocal()
    try:
        user = verify_ws_token(token, db)
        if user is None or user.role != "ri":
            await websocket.close(code=4003)  # forbidden
            return
        if not _version_exists(db, version_id):
            await websocket.close(code=4004)  # not found
            return
        snapshot = _board(db, version_id).model_dump(mode="json")
    finally:
        db.close()

    await websocket.accept()
    await registry.connect(version_id, websocket, user.id)
    try:
        await websocket.send_json({"type": "state_changed", "board": snapshot})
        # Actions flow through POST. Inbound frames carry only the E6 presence annotation
        # (CR-NS-038) — handled silently; the loop still drains to detect disconnect, exactly as before.
        while True:
            await _apply_ws_presence_frame(version_id, websocket, await websocket.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        await registry.disconnect(version_id, websocket)
