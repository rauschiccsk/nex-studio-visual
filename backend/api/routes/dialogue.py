"""REST + WebSocket router for ``/api/v1/dialogue/*``.

Director-mediated Customer ↔ Designer dialogue (Gate E). Plný-gate
mode: Director approves every message before it's delivered to the
recipient agent.

Endpoints (all require ``ri`` role):

* ``POST   /sessions``                            — create new session
* ``GET    /sessions``                            — list user's active sessions
* ``GET    /sessions/{id}``                       — session + all messages (rehydrate)
* ``DELETE /sessions/{id}``                       — end session
* ``POST   /sessions/{id}/customer-next-question`` — trigger Customer to ask next
* ``POST   /sessions/{id}/director-inject``        — Director injects own message
* ``POST   /messages/{id}/approve``                — Director approves pending
* ``POST   /messages/{id}/reject``                 — Director rejects pending
* ``WS     /sessions/{id}/stream?token=<jwt>``     — real-time event stream

WS protocol (server → client):
    ``{"type": "message", "message_id": "..."}`` — new pending message
    ``{"type": "message_updated", "message_id": "..."}`` — status change
    ``{"type": "session_ended"}`` — session terminated
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

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
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core.security import require_ri_role
from backend.db.models.dialogue import DialogueMessage, DialogueSession
from backend.db.models.foundation import User
from backend.db.session import SessionLocal, get_db
from backend.schemas.dialogue import (
    DialogueMessageRead,
    DialogueSessionCreate,
    DialogueSessionRead,
    DialogueSessionWithMessages,
    DirectorInjectMessage,
)
from backend.services import dialogue as service
from backend.services.dialogue import (
    DialogueError,
    DialogueSessionNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Dialogue"])


# ---------------------------------------------------------------------------
# REST — session lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=DialogueSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: DialogueSessionCreate,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueSession:
    """Director starts a fresh Gate E session — spawns both agents."""
    try:
        row = await service.create_session(
            user_id=current_user.id,
            project_slug=payload.project_slug,
            version_id=payload.version_id,
            db=db,
        )
    except DialogueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return row


@router.get("/sessions", response_model=list[DialogueSessionRead])
def list_sessions(
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> list[DialogueSession]:
    """List sessions owned by the current user, newest first."""
    rows = (
        db.execute(
            select(DialogueSession)
            .where(DialogueSession.user_id == current_user.id)
            .order_by(DialogueSession.created_at.desc()),
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get(
    "/sessions/{session_id}",
    response_model=DialogueSessionWithMessages,
)
def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueSessionWithMessages:
    """Session detail + all messages (chronological)."""
    sess = db.get(DialogueSession, session_id)
    if sess is None or sess.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    messages = (
        db.execute(
            select(DialogueMessage)
            .where(DialogueMessage.session_id == session_id)
            .order_by(DialogueMessage.created_at.asc()),
        )
        .scalars()
        .all()
    )
    return DialogueSessionWithMessages(
        **DialogueSessionRead.model_validate(sess).model_dump(),
        messages=[DialogueMessageRead.model_validate(m) for m in messages],
    )


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
    """Explicit End — SIGTERM both agents + grace + SIGKILL."""
    sess = db.get(DialogueSession, session_id)
    if sess is None or sess.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if sess.status != "ended":
        await service.end_session(
            session_id=session_id,
            terminated_by="user",
            db=db,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# REST — message lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/customer-next-question",
    response_model=DialogueMessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_customer_next_question(
    session_id: uuid.UUID,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueMessage:
    """Tell the Customer agent to produce its next question.

    Sends a literal "Next question." prompt to Customer's stdin; the
    reader task collects Customer's response, settles after
    :data:`SETTLE_SECONDS`, and persists as a ``pending`` message
    authored by ``customer``.
    """
    sess = _verify_session_owner(session_id, current_user, db)
    if sess.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {sess.status}, cannot trigger next question",
        )
    try:
        await service.send_to_agent(
            session_id=session_id,
            recipient="customer",
            content="Next question.",
        )
    except DialogueSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Customer agent not running",
        ) from exc
    # The reader task will eventually persist Customer's response; we
    # return a placeholder telling the caller the prompt was sent.
    # The frontend listens for the WS ``message`` event for the real
    # message row.
    return DialogueMessage(
        session_id=session_id,
        author="customer",
        content="(pending — Customer is generating)",
        status="pending",
    )


@router.post(
    "/sessions/{session_id}/director-inject",
    response_model=DialogueMessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def director_inject_message(
    session_id: uuid.UUID,
    payload: DirectorInjectMessage,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueMessage:
    """Director sends their own message directly to one of the agents.

    Director-authored messages skip the ``pending → approved`` gate —
    they go straight to ``delivered``, and the content is forwarded to
    the recipient agent's stdin.
    """
    sess = _verify_session_owner(session_id, current_user, db)
    if sess.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {sess.status}, cannot inject",
        )
    msg = service.add_message(
        session_id=session_id,
        author="director",
        content=payload.content,
        status="delivered",
        db=db,
    )
    try:
        await service.send_to_agent(
            session_id=session_id,
            recipient=payload.recipient,
            content=payload.content,
        )
    except DialogueSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{payload.recipient} agent not running",
        ) from exc
    return msg


@router.post(
    "/messages/{message_id}/approve",
    response_model=DialogueMessageRead,
)
async def approve_message(
    message_id: uuid.UUID,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueMessage:
    """Director approves a ``pending`` message → ``delivered``.

    After approval the content is forwarded to the recipient agent
    (the one that didn't author this message — Customer's question
    goes to Designer; Designer's reply goes to Customer).
    """
    msg = db.get(DialogueMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Message not found")
    _verify_session_owner(msg.session_id, current_user, db)

    try:
        service.approve_message(message_id, db)
    except DialogueError as exc:
        raise HTTPException(400, str(exc)) from exc

    recipient = "designer" if msg.author == "customer" else "customer"
    try:
        await service.send_to_agent(
            session_id=msg.session_id,
            recipient=recipient,
            content=msg.content,
        )
        service.mark_delivered(message_id, db)
    except DialogueSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{recipient} agent not running",
        ) from exc
    db.refresh(msg)
    return msg


@router.post(
    "/messages/{message_id}/reject",
    response_model=DialogueMessageRead,
)
def reject_message(
    message_id: uuid.UUID,
    current_user: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> DialogueMessage:
    """Director rejects a ``pending`` message → ``rejected``.

    The recipient agent never sees the message. Frontend can then
    trigger ``customer-next-question`` if it was Customer's question
    that got rejected, asking Customer to reformulate.
    """
    msg = db.get(DialogueMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Message not found")
    _verify_session_owner(msg.session_id, current_user, db)
    try:
        return service.reject_message(message_id, db)
    except DialogueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# WebSocket — real-time event stream
# ---------------------------------------------------------------------------


def _verify_session_owner(
    session_id: uuid.UUID,
    user: User,
    db: Session,
) -> DialogueSession:
    sess = db.get(DialogueSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return sess


def _verify_ws_token(token: str, db: Session) -> Optional[User]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
        )
        user_id = uuid.UUID(str(payload["sub"]))
    except (JWTError, KeyError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


@router.websocket("/sessions/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    """Real-time event stream for the /dialogue page.

    Server pushes ``{"type": "message", "message_id": "..."}`` whenever
    an agent's settled output gets persisted as a new pending message.
    Frontend fetches the full message via REST and updates the UI.
    """
    db = SessionLocal()
    try:
        user = _verify_ws_token(token, db)
        if user is None or user.role != "ri":
            await websocket.close(code=4003)
            return
        sess = db.get(DialogueSession, session_id)
        if sess is None or sess.user_id != user.id or sess.status == "ended":
            await websocket.close(code=4004)
            return
    finally:
        db.close()

    await websocket.accept()
    try:
        q = await service.subscribe(session_id)
    except DialogueSessionNotFoundError:
        await websocket.close(code=4005)
        return

    try:
        while True:
            payload = await q.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        await service.unsubscribe(session_id, q)
