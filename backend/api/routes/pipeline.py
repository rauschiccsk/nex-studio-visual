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
    AutonomousDecisionsSummary,
    BoardTask,
    CoordinatorTriage,
    FastFixStartRequest,
    FastFixStartResponse,
    PipelineActionRequest,
    PipelineBoardRead,
    PipelineMessageRead,
    PipelineStateRead,
    RegateProposal,
)
from backend.services import agent_terminal as agent_terminal_service
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
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    # WS-C1 (CR-NS-030): build-readiness facts for the FE to disable the final-approve / end-build
    # buttons when not satisfiable (the state-only available_actions can't see todos / open findings).
    all_tasks_done, build_open_findings = (
        orchestrator.build_readiness(db, version_id) if state is not None else (True, 0)
    )
    # gate-g-hardening GAP 1 (A4): the gate_g PASS-button gate — True (permissive) everywhere except a
    # gate_g where the engine release acceptance has not yet reached exit-0 / a legit SKIP this iteration.
    release_acceptance_satisfied = (
        orchestrator._release_acceptance_satisfied(db, version_id)
        if (state is not None and state.current_stage == "gate_g")
        else True
    )
    # WS-C2 (CR-NS-035): the build task in focus for the "kto je na rade" board (only at build).
    ct = (
        orchestrator.current_build_task(db, version_id)
        if (state is not None and state.current_stage == "build")
        else None
    )
    # gate_g FAIL Fix 2 (CR-NS-057 §F2.4): propose the re-gate target, computed FRESH (no synthesis payload
    # marker), only at gate_g / awaiting_director|blocked. None elsewhere → the FE shows a plain FAIL verdict.
    regate_proposal = None
    if state is not None and state.current_stage == "gate_g" and state.status in ("awaiting_director", "blocked"):
        entry = orchestrator._infer_regate_entry_stage(db, version_id)
        reason = "návrh/rozsah → späť na dizajn (gate_a)" if entry == "gate_a" else "oprava implementácie → znova build"
        regate_proposal = RegateProposal(entry_stage=entry, reason=reason)
    # R4 operator legibility (v0.7.0, D3/D4/D5): the Coordinator's current triage, a board roll-up of
    # autonomous decisions, and per-role agent liveness — each a bounded per-version scan / one query.
    triage = orchestrator.coordinator_triage(db, version_id, state)
    autonomous = orchestrator.autonomous_decisions_summary(db, version_id) if state is not None else None
    sessions = orchestrator.agent_sessions(db, version_id, state) if state is not None else []
    return PipelineBoardRead(
        state=PipelineStateRead.model_validate(state) if state is not None else None,
        recent_messages=[PipelineMessageRead.model_validate(m) for m in _recent_messages(db, version_id, limit)],
        gate_e_open_findings=orchestrator._gate_e_open_findings(db, version_id),
        # WS-C1 (CR-NS-030): backend-authoritative offerable actions so the FE can't show no-op buttons.
        available_actions=sorted(orchestrator.determine_available_actions(state)) if state is not None else [],
        all_tasks_done=all_tasks_done,
        build_open_findings=build_open_findings,
        release_acceptance_satisfied=release_acceptance_satisfied,
        current_task=BoardTask(number=ct.number, title=ct.title) if ct is not None else None,
        regate_proposal=regate_proposal,
        coordinator_triage=CoordinatorTriage(**triage) if triage is not None else None,
        autonomous_decisions_summary=AutonomousDecisionsSummary(**autonomous) if autonomous is not None else None,
        agent_sessions=[AgentSession(**s) for s in sessions],
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
    """Fast-Fix Lane entry (F-009, CR-NS-094) — the "Rýchla oprava" one-prompt action.

    Auto-creates the next PATCH version (``vX.Y.Z+1`` from the project's semver max) and starts a
    ``fast_fix`` pipeline carrying the Director directive; the kickoff Coordinator triages it
    (escalation guard) and the board then shows the short fast-lane path. Declared before the
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
    # The Director's return/ask/answer content (and the Coordinator's report for
    # apply_coordinator_recommendation) is threaded into the re-dispatch prompt so
    # the agent acts on it (else it re-runs blind on the generic directive);
    # fresh-stage dispatch (start/approve/verdict) → directive None.
    if state.status == "agent_working":
        directive = orchestrator.dispatch_directive(
            db, version_id, payload.action, payload.payload or {}, state.current_stage
        )
        # Gate E sub-flow (F-007-gate-e §2/§5): fix → Designer edits (Coordinator-relayed)
        # then continues; ask/return @ gate_e → the Coordinator revises its recommendation.
        gate_e_dispatch = None
        if state.current_stage == "gate_e":
            if payload.action == "fix":
                gate_e_dispatch = "designer_edit"
            elif payload.action in ("ask", "return"):
                gate_e_dispatch = "coordinator_consult"
        pipeline_runner.schedule_dispatch(version_id, directive, gate_e_dispatch=gate_e_dispatch)

    return _board(db, version_id)


@router.post("/{version_id}/debug-terminal", response_model=AgentTerminalSessionRead)
async def open_debug_terminal(
    version_id: uuid.UUID,
    role: str = Query(..., description="orchestrator agent role to attach to"),
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> AgentTerminalSessionRead:
    """Attach an interactive Director terminal to the headless agent session.

    Resumes the existing ``orchestrator_session.claude_session_id`` for
    ``(project, role)`` into a Director-owned ``agent_terminal_sessions`` row
    so the standard AgentTerminal WS can stream it (F-007 §10 debug hatch).
    The Director observes; the orchestrator still drives the pipeline.
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
