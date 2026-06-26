"""REST + WebSocket router for ``/api/v1/agent-terminal/*``.

REST endpoints (Director-only, ``require_ri_role``):

* ``POST /spawn`` — spawn a new claude CLI process under PTY for the
  given ``(role, project_slug)``. Returns the persisted session row.
* ``GET /sessions`` — list active sessions for the current user.
* ``DELETE /sessions/{id}`` — explicit "End session" (SIGTERM + grace
  + SIGKILL). Idempotent.

WebSocket endpoint:

* ``WS /ws/{session_id}?token=<jwt>`` — stream output bytes to the
  client and forward input bytes from the client to the PTY.

WebSocket auth uses a query-string ``token`` because the browser
``WebSocket`` constructor cannot set custom headers. The token format
matches the REST ``Authorization: Bearer <jwt>`` flow — same HS256
signature, same ``sub`` (user id) + ``tv`` (token version) claims.

JSON message protocol (both directions on the WS):

* From client to server:
    ``{"type": "input", "data": "<utf-8 string>"}`` — keystrokes
    ``{"type": "resize", "rows": int, "cols": int}`` — winsize update
* From server to client:
    ``{"type": "output", "data": "<utf-8 string>"}`` — raw PTY bytes,
        decoded with ``errors="replace"`` so partial UTF-8 mid-chunk
        doesn't break rendering
    ``{"type": "end", "exit_code": int|null, "terminated_by": str}``
        — session ended; the server then closes the WS
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import require_ri_role, verify_ws_token
from backend.db.models.agent_terminal import AgentTerminalSession
from backend.db.models.foundation import User
from backend.db.session import SessionLocal, get_db
from backend.schemas.agent_terminal import (
    AgentTerminalSessionRead,
    AgentTerminalSpawnRequest,
)
from backend.services import agent_terminal as service
from backend.services.agent_terminal import (
    AgentTerminalError,
    SessionConflictError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent Terminal"])


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/spawn",
    response_model=AgentTerminalSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def spawn_session(
    payload: AgentTerminalSpawnRequest,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> AgentTerminalSession:
    """Spawn a fresh claude CLI process for ``(role, project_slug)``."""
    try:
        row = await service.spawn(
            user_id=current_user.id,
            role=payload.role,
            project_slug=payload.project_slug,
            db=db,
        )
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AgentTerminalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return row


@router.get("/available-roles", response_model=dict[str, bool])
def available_roles(
    project_slug: str = Query(..., description="Project slug to check charter availability for."),
    _current_user: User = Depends(require_ri_role),
) -> dict[str, bool]:
    """Return per-role charter availability for ``project_slug``.

    Since CR-V2-007 the spawn API is AI-Agent-only, so this reports just
    ``{"ai-agent": <bool>}`` — true when ``.claude/agents/ai-agent/CLAUDE.md``
    exists in the project (the set mirrors ``_VALID_ROLES``). An invalid slug or
    unknown project → 404.
    """
    try:
        return service.available_roles(project_slug)
    except AgentTerminalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/sessions", response_model=list[AgentTerminalSessionRead])
def list_sessions(
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> list[AgentTerminalSession]:
    """List active sessions (``ended_at IS NULL``) for the current user."""
    rows = (
        db.execute(
            select(AgentTerminalSession)
            .where(
                AgentTerminalSession.user_id == current_user.id,
                AgentTerminalSession.ended_at.is_(None),
            )
            .order_by(AgentTerminalSession.created_at.desc()),
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def end_session(
    session_id: uuid.UUID,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> Response:
    """Explicit End session — SIGTERM, grace, SIGKILL. Idempotent."""
    row = db.get(AgentTerminalSession, session_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if row.ended_at is None:
        await service.end_session(session_id, terminated_by="user", db=db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/{session_id}")
async def terminal_ws(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    """Bidirectional WS: output bytes ← server, input bytes → server."""
    # Auth + session ownership check (close before accept where possible).
    db = SessionLocal()
    try:
        user = verify_ws_token(token, db)
        if user is None or user.role != "ri":
            await websocket.close(code=4003)  # forbidden
            return
        row = db.get(AgentTerminalSession, session_id)
        if row is None or row.user_id != user.id:
            await websocket.close(code=4004)  # not found
            return
        if row.ended_at is not None:
            await websocket.close(code=4005)  # session already ended
            return
    finally:
        db.close()

    await websocket.accept()

    async def _output_writer() -> None:
        """Pump PTY bytes → WS as JSON ``output`` frames."""
        try:
            async for chunk in service.attach(session_id):
                await websocket.send_json(
                    {
                        "type": "output",
                        "data": chunk.decode("utf-8", errors="replace"),
                    },
                )
        except SessionNotFoundError:
            pass
        # When attach() returns, the session has ended.
        try:
            await websocket.send_json(
                {
                    "type": "end",
                    "exit_code": None,
                    "terminated_by": "exited",
                },
            )
        except Exception:  # noqa: BLE001 — WS may already be closed
            pass

    async def _input_reader() -> None:
        """Pump WS JSON frames → PTY (input) / service (resize)."""
        try:
            while True:
                msg = await websocket.receive_json()
                msg_type = msg.get("type")
                if msg_type == "input":
                    data = msg.get("data", "")
                    if isinstance(data, str) and data:
                        await service.write_input(session_id, data.encode("utf-8"))
                elif msg_type == "resize":
                    rows = int(msg.get("rows", 40))
                    cols = int(msg.get("cols", 120))
                    await service.resize(session_id, rows, cols)
        except WebSocketDisconnect:
            return
        except SessionNotFoundError:
            return

    writer_task = asyncio.create_task(_output_writer())
    reader_task = asyncio.create_task(_input_reader())

    done, pending = await asyncio.wait(
        {writer_task, reader_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    try:
        await websocket.close()
    except Exception:  # noqa: BLE001
        pass
