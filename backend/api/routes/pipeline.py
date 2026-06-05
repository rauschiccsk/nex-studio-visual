"""REST + WebSocket router for the Orchestration Cockpit (F-007 §6, CR-NS-018 Phase 3).

* ``GET    /pipeline/{version_id}``          → board (state + last N messages)
* ``GET    /pipeline/{version_id}/messages`` → paginated message log
* ``POST   /pipeline/{version_id}/action``   → Director action → orchestrator,
  then broadcasts ``state_changed`` + ``message_added`` to live board sockets.
* ``WS     /pipeline/ws/{version_id}?token`` → live board feed + §9 presence.

All Director-only (``require_ri_role`` / ``verify_ws_token`` + ``role == 'ri'``).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.security import require_ri_role, verify_ws_token
from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.versions import Version
from backend.db.session import SessionLocal, get_db
from backend.schemas.agent_terminal import AgentTerminalSessionRead
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.pipeline import (
    PipelineActionRequest,
    PipelineBoardRead,
    PipelineMessageRead,
    PipelineStateRead,
)
from backend.services import agent_terminal as agent_terminal_service
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
    return PipelineBoardRead(
        state=PipelineStateRead.model_validate(state) if state is not None else None,
        recent_messages=[PipelineMessageRead.model_validate(m) for m in _recent_messages(db, version_id, limit)],
    )


def _map_orch_error(exc: OrchestratorError) -> HTTPException:
    msg = str(exc)
    lowered = msg.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    if "already started" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


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
        # Gate E Branch B fix: the Designer edits first (Coordinator-relayed directive),
        # then the round continues to the next question (F-007-gate-e §2).
        pipeline_runner.schedule_dispatch(version_id, directive, designer_edit=(payload.action == "fix"))

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

    slug = orchestrator._project_slug_for_version(db, version_id)
    orch = db.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == slug,
            OrchestratorSession.role == role,
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
        # Actions flow through POST; inbound WS frames are ignored (drain to
        # detect disconnect).
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await registry.disconnect(version_id, websocket)
