"""Project-scoped Architect session endpoints and message SSE streaming.

Implements the DESIGN.md §3.1 ``ArchitectPage`` API surface — session
create, list, detail and close — with ``ri``-only guards on mutating
operations (DESIGN.md D-11 authorisation note).  Also provides the
Architect AI message endpoint with SSE streaming.

The router spans two URL families:

* ``/projects/{project_id}/architect``   — create (POST) and list (GET)
* ``/architect/sessions/{session_id}``   — detail (GET), close (POST),
  message (POST), messages list (GET)

It is therefore mounted with the bare ``/api/v1`` prefix in
``backend/main.py``, mirroring the ``versions`` router pattern.

Most endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The SSE streaming endpoint (``POST .../message``) is ``async def``
because it consumes an async generator from the Claude subprocess; DB
operations within it use a dedicated sync session obtained from
:func:`~backend.db.session.SessionLocal`.

The router delegates every persistence operation to
:mod:`backend.services.architect_session` and handles commit / rollback
itself so the service layer stays transaction-agnostic.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.session import SessionLocal, get_db
from backend.schemas.architect import (
    ArchitectMessageSend,
    ArchitectSessionCreate,
    ArchitectSessionRead,
)
from backend.schemas.architect_message import ArchitectMessageRead
from backend.schemas.pagination import PaginatedResponse
from backend.services import architect_context as architect_context_service
from backend.services import architect_message as architect_message_service
from backend.services import architect_session as architect_session_service
from backend.services import claude_subprocess
from backend.services import project as project_service

logger = logging.getLogger(__name__)

# Maximum number of recent messages to include in Claude context window.
CONVERSATION_HISTORY_LIMIT = 100

router = APIRouter(tags=["Architect"])


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates / conflicts → 409, everything else → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _require_project_exists(
    db: Session,
    project_id: UUID,
) -> None:
    """Raise HTTP 404 if the project does not exist.

    Returns 404 to avoid leaking project existence — standard security
    practice (DESIGN.md §4.1).
    """
    try:
        project_service.get_by_id(db, project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )


# ------------------------------------------------------------------ project-scoped


@router.post(
    "/projects/{project_id}/architect",
    response_model=ArchitectSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_architect_session(
    project_id: UUID,
    payload: ArchitectSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> ArchitectSessionRead:
    """Open a new Architect chat session for a project.

    ``ri`` role only (DESIGN.md D-11).  ``project_id`` comes from the
    URL path; ``created_by`` is the authenticated user.  ``module_id``
    is optional — ``None`` opens a Foundation / project-level session.
    """
    _require_project_exists(db, project_id)
    try:
        session_obj = architect_session_service.create_session(
            db,
            project_id=project_id,
            user_id=current_user.id,
            module_id=payload.module_id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(session_obj)
    return ArchitectSessionRead.model_validate(session_obj)


@router.get(
    "/projects/{project_id}/architect",
    response_model=PaginatedResponse[ArchitectSessionRead],
)
def list_project_architect_sessions(
    project_id: UUID,
    module_id: Optional[UUID] = Query(
        default=None,
        description="Filter by module scope.",
    ),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (active | closed).",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[ArchitectSessionRead]:
    """Return a paginated list of Architect sessions for a project."""
    _require_project_exists(db, project_id)
    try:
        rows = architect_session_service.list_architect_sessions(
            db,
            project_id=project_id,
            module_id=module_id,
            status=status_filter,
            limit=limit,
            offset=skip,
        )
        total = architect_session_service.count_architect_sessions(
            db,
            project_id=project_id,
            module_id=module_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ArchitectSessionRead](
        items=[ArchitectSessionRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


# ------------------------------------------------------------------ session-scoped


@router.get(
    "/architect/sessions/{session_id}",
    response_model=ArchitectSessionRead,
)
def get_architect_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArchitectSessionRead:
    """Return a single Architect session by primary key."""
    try:
        session_obj = architect_session_service.get_session(db, session_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    _require_project_exists(db, session_obj.project_id)
    return ArchitectSessionRead.model_validate(session_obj)


@router.post(
    "/architect/sessions/{session_id}/close",
    response_model=ArchitectSessionRead,
)
def close_architect_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> ArchitectSessionRead:
    """Close an active Architect session.

    ``ri`` role only (DESIGN.md D-11).  Transitions status to
    ``closed`` and auto-stamps ``closed_at``.  Returns HTTP 409 if
    the session is already closed.
    """
    # Look up session to verify project existence before mutation.
    try:
        session_obj = architect_session_service.get_session(db, session_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    _require_project_exists(db, session_obj.project_id)
    try:
        session_obj = architect_session_service.close_session(db, session_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(session_obj)
    return ArchitectSessionRead.model_validate(session_obj)


# ------------------------------------------------------------------ messages


@router.get(
    "/architect/sessions/{session_id}/messages",
    response_model=PaginatedResponse[ArchitectMessageRead],
)
def list_session_messages(
    session_id: UUID,
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[ArchitectMessageRead]:
    """Return a paginated list of messages for an Architect session.

    Messages are ordered by ``created_at ASC`` (conversation order).
    """
    try:
        # Validate session exists and check project existence
        session_obj = architect_session_service.get_session(db, session_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    _require_project_exists(db, session_obj.project_id)
    try:
        rows = architect_message_service.list_architect_messages(
            db,
            session_id=session_id,
            limit=limit,
            offset=skip,
        )
        total = architect_message_service.count_architect_messages(
            db,
            session_id=session_id,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ArchitectMessageRead](
        items=[ArchitectMessageRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/architect/sessions/{session_id}/message",
    status_code=status.HTTP_200_OK,
)
async def send_architect_message(
    session_id: UUID,
    payload: ArchitectMessageSend,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Send a user message to the Architect AI and stream the response via SSE.

    ``ri`` role only (DESIGN.md D-11).  Accepts ``{content: str}``,
    builds context via :func:`architect_context.build_architect_context`,
    persists the user message, then streams the AI response as SSE events::

        data: {"type": "chunk", "content": "..."}
        data: {"type": "done", "content": "...full text...", "tokens": {...}}

    After the stream completes the assistant message is persisted with
    token counts into ``architect_messages``.
    """
    # --- Validate session and build context (sync DB) ---
    try:
        session_obj = architect_session_service.get_session(db, session_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    _require_project_exists(db, session_obj.project_id)

    if session_obj.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session {session_id} is closed — cannot send messages.",
        )

    try:
        context = architect_context_service.build_architect_context(
            db,
            project_id=session_obj.project_id,
            module_id=session_obj.module_id,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    # --- Persist user message ---
    try:
        architect_session_service.add_message(
            db,
            session_id=session_id,
            role="user",
            content=payload.content,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc

    # --- Build conversation history for context ---
    history_messages = architect_message_service.list_architect_messages(
        db,
        session_id=session_id,
        limit=CONVERSATION_HISTORY_LIMIT,
        offset=0,
    )
    conversation_parts: list[str] = []
    for msg in history_messages:
        role_label = "User" if msg.role == "user" else "Assistant"
        conversation_parts.append(f"[{role_label}]: {msg.content}")
    conversation_history = "\n\n".join(conversation_parts)

    full_context = f"{context}\n\n---\n\n## Conversation History\n\n{conversation_history}"

    # --- Stream SSE response ---
    async def _sse_generator():
        full_content: list[str] = []
        error_occurred = False
        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=payload.content,
                context=full_context,
            ):
                full_content.append(chunk)
                event_data = json.dumps({"type": "chunk", "content": chunk})
                yield f"data: {event_data}\n\n"
        except (RuntimeError, TimeoutError) as exc:
            error_occurred = True
            logger.error("Claude stream error for session %s: %s", session_id, exc)
            error_data = json.dumps({"type": "error", "content": str(exc)})
            yield f"data: {error_data}\n\n"

        # Persist assistant message after stream completes
        assistant_content = "".join(full_content)
        if assistant_content and not error_occurred:
            persist_db = SessionLocal()
            try:
                architect_session_service.add_message(
                    persist_db,
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    # Token counts are not available from CLI streaming —
                    # can be backfilled via PATCH on the message later.
                    input_tokens=None,
                    output_tokens=None,
                    cost_usd=None,
                )
                persist_db.commit()
            except Exception:
                persist_db.rollback()
                logger.exception(
                    "Failed to persist assistant message for session %s",
                    session_id,
                )
            finally:
                persist_db.close()

        done_data = json.dumps(
            {
                "type": "done",
                "content": assistant_content,
                "tokens": {
                    "input_tokens": None,
                    "output_tokens": None,
                },
            }
        )
        yield f"data: {done_data}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
