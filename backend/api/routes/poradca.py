"""Poradca — rozhovory s agentom, ktorý len číta a radí (ICCINT-167, ``docs/specs/poradca.md``).

* ``GET   /status``                                   → či Poradca vie bežať (a prečo nie)
* ``GET   /projects/{slug}/conversations``            → rozhovory k projektu (moje; admin všetky)
* ``POST  /projects/{slug}/conversations``            → nový rozhovor s prvou otázkou
* ``GET   /conversations/{id}``                       → rozhovor so správami
* ``PATCH /conversations/{id}``                       → o čom sa rozprávame (verzia / celý projekt)
* ``PUT   /conversations/{id}/title``                 → premenovať
* ``DELETE /conversations/{id}``                      → vymazať (text preč, cena ostáva v Nákladoch)
* ``POST  /conversations/{id}/messages``              → ďalšia otázka
* ``POST  /messages/{id}/stop``                        → zastaviť odpoveď
* ``WS    /conversations/{id}/ws?token``               → živý priebeh (kroky, stav)

Prístup: k projektu jeho vlastník alebo účet admin (ako všade v kokpite); k rozhovoru jeho autor alebo
admin. Iný človek dostane 404 — o cudzom rozhovore sa nemá dozvedieť ani to, že existuje. Vymazaný
rozhovor je 404 pre každého; jeho riadok ostal len kvôli Nákladom.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, verify_ws_token
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.poradca import AUTHOR_PORADCA, RUNNING, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal, get_db
from backend.schemas.poradca import (
    PoradcaAsk,
    PoradcaConversationCreate,
    PoradcaConversationDetail,
    PoradcaConversationRead,
    PoradcaMessageRead,
    PoradcaNewVersion,
    PoradcaProjectContext,
    PoradcaRename,
    PoradcaScopeUpdate,
    PoradcaStatus,
    PoradcaVersionInfo,
)
from backend.services import metrics
from backend.services.poradca import handoff, readiness, runner

router = APIRouter(tags=["Poradca"])

_TITLE_CHARS = 80


def _title(question: str) -> str:
    first = " ".join(question.strip().split())
    return first if len(first) <= _TITLE_CHARS else first[: _TITLE_CHARS - 1] + "…"


def _author_name(user: Optional[User]) -> str:
    if user is None:
        return "—"
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.username


def _check_version(db: Session, project: Project, version_id: Optional[uuid.UUID]) -> None:
    if version_id is None:
        return
    version = db.get(Version, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Verzia nepatrí k tomuto projektu.")


def _conversation_for(db: Session, user: User, conversation_id: uuid.UUID) -> tuple[PoradcaConversation, Project]:
    conversation = db.get(PoradcaConversation, conversation_id)
    if (
        conversation is None
        or conversation.deleted_at is not None
        or not (conversation.author_id == user.id or authz.is_admin(user))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.")
    project = authz.assert_project_id_access(db, user, conversation.project_id)
    return conversation, project


def _message_read(db: Session, msg: PoradcaMessage) -> PoradcaMessageRead:
    usage = msg.usage or {}
    done_answer = msg.author == AUTHOR_PORADCA and msg.status == "done"
    cost = None
    if msg.author == AUTHOR_PORADCA and usage:
        cost = metrics.usage_cost(
            db, usage.get("model"), int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        )
    return PoradcaMessageRead(
        id=msg.id,
        author=msg.author,
        content=msg.content,
        steps=msg.steps or [],
        status=msg.status,
        model=usage.get("model"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cost=cost,
        duration_seconds=msg.duration_seconds,
        error=msg.error,
        created_at=msg.created_at,
        finished_at=msg.finished_at,
        instruction=handoff.extract_block(msg.content, handoff.BLOCK_INSTRUCTION) if done_answer else None,
        new_version_request=handoff.extract_block(msg.content, handoff.BLOCK_NEW_VERSION) if done_answer else None,
        captured_version_id=msg.captured_version_id,
    )


def _conversation_read(db: Session, conversation: PoradcaConversation) -> PoradcaConversationRead:
    version = db.get(Version, conversation.version_id) if conversation.version_id else None
    running = (
        db.execute(
            select(PoradcaMessage.id).where(
                PoradcaMessage.conversation_id == conversation.id, PoradcaMessage.status == RUNNING
            )
        ).first()
        is not None
    )
    return PoradcaConversationRead(
        id=conversation.id,
        project_id=conversation.project_id,
        version_id=conversation.version_id,
        version_number=version.version_number if version else None,
        author_id=conversation.author_id,
        author_name=_author_name(db.get(User, conversation.author_id)),
        title=conversation.title,
        running=running,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _detail(db: Session, conversation: PoradcaConversation) -> PoradcaConversationDetail:
    messages = (
        db.execute(
            select(PoradcaMessage)
            .where(PoradcaMessage.conversation_id == conversation.id)
            .order_by(PoradcaMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    return PoradcaConversationDetail(
        **_conversation_read(db, conversation).model_dump(),
        messages=[_message_read(db, m) for m in messages],
    )


def _ask(db: Session, conversation: PoradcaConversation, question: str, user: User) -> None:
    ready = readiness.status(db)
    if not ready.ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Poradca teraz nevie bežať: " + "; ".join(ready.problems)
        )
    try:
        runner.ask(db, conversation, question, user)
    except runner.PoradcaBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except runner.ConversationGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.") from exc


def _edit(db: Session, conversation: PoradcaConversation, **values: object) -> None:
    try:
        runner.edit_conversation(db, conversation, **values)
    except runner.ConversationGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.") from exc


@router.get("/status", response_model=PoradcaStatus)
def poradca_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> PoradcaStatus:
    return readiness.status(db)


def _instruction_target(state: Optional[PipelineState]) -> tuple[bool, Optional[str]]:
    """Či pole Riadiaceho centra tejto verzie prijme pokyn — tie isté pravidlá, aké platia pre pole samo."""
    if state is None:
        return False, "Stavba tejto verzie ešte nezačala — pokyn nemá komu ísť. Začni stavbu v Riadiacom centre."
    if state.current_stage == "done":
        return False, "Verzia je hotová — zmena patrí do novej verzie."
    if state.status == "blocked" and state.block_reason == "framework_issue":
        return False, "Stavba čaká na opravu kokpitu — pole v Riadiacom centre je zatvorené."
    return True, None


@router.get("/projects/{slug}/context", response_model=PoradcaProjectContext)
def project_context(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaProjectContext:
    """Projekt a jeho verzie s fázou a stavom stavby — pre voľbu „o čom sa rozprávame"."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    rows = db.execute(
        select(Version, PipelineState)
        .outerjoin(PipelineState, PipelineState.version_id == Version.id)
        .where(Version.project_id == project.id)
        .order_by(Version.created_at.desc())
    ).all()
    versions = []
    for version, state in rows:
        open_, reason = _instruction_target(state)
        versions.append(
            PoradcaVersionInfo(
                id=version.id,
                version_number=version.version_number,
                stage=state.current_stage if state else None,
                status=state.status if state else None,
                instruction_open=open_,
                instruction_closed_reason=reason,
            )
        )
    return PoradcaProjectContext(
        project_id=project.id, project_slug=project.slug, project_name=project.name, versions=versions
    )


@router.get("/projects/{slug}/conversations", response_model=list[PoradcaConversationRead])
def list_conversations(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[PoradcaConversationRead]:
    """Moje rozhovory k projektu, najnovší prvý. Admin vidí rozhovory všetkých."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    query = select(PoradcaConversation).where(
        PoradcaConversation.project_id == project.id, PoradcaConversation.deleted_at.is_(None)
    )
    if not authz.is_admin(current_user):
        query = query.where(PoradcaConversation.author_id == current_user.id)
    rows = db.execute(query.order_by(PoradcaConversation.updated_at.desc())).scalars().all()
    return [_conversation_read(db, c) for c in rows]


@router.post(
    "/projects/{slug}/conversations", response_model=PoradcaConversationDetail, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    slug: str,
    payload: PoradcaConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaConversationDetail:
    project = authz.assert_project_slug_access(db, current_user, slug)
    _check_version(db, project, payload.version_id)
    conversation = runner.new_conversation(
        db, project=project, author=current_user, version_id=payload.version_id, title=_title(payload.question)
    )
    db.commit()
    _ask(db, conversation, payload.question, current_user)
    return _detail(db, conversation)


@router.get("/conversations/{conversation_id}", response_model=PoradcaConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaConversationDetail:
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    return _detail(db, conversation)


@router.patch("/conversations/{conversation_id}", response_model=PoradcaConversationRead)
def update_scope(
    conversation_id: uuid.UUID,
    payload: PoradcaScopeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaConversationRead:
    conversation, project = _conversation_for(db, current_user, conversation_id)
    _check_version(db, project, payload.version_id)
    _edit(db, conversation, version_id=payload.version_id)
    return _conversation_read(db, conversation)


@router.put("/conversations/{conversation_id}/title", response_model=PoradcaConversationRead)
def rename_conversation(
    conversation_id: uuid.UUID,
    payload: PoradcaRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaConversationRead:
    """Premenovať rozhovor. Poradie v zozname sa nemení — určuje ho posledná otázka."""
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    _edit(db, conversation, title=payload.title)
    return _conversation_read(db, conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_conversation(
    conversation_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Response:
    """Vymazať rozhovor: otázky, odpovede, kroky aj záznam na disku sú preč natrvalo; cena ostáva v Nákladoch.

    Kým Poradca odpovedá, 409 — najprv treba odpoveď zastaviť.
    """
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    try:
        await runner.delete_conversation(db, conversation)
    except runner.PoradcaBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except runner.ConversationGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.") from exc
    except OSError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Záznam rozhovoru na disku sa nepodarilo vymazať; rozhovor ostal celý. Skús to znova.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages", response_model=PoradcaConversationDetail)
async def ask(
    conversation_id: uuid.UUID,
    payload: PoradcaAsk,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaConversationDetail:
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    _ask(db, conversation, payload.question, current_user)
    return _detail(db, conversation)


@router.post("/messages/{message_id}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_message(
    message_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    msg = db.get(PoradcaMessage, message_id)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Správa sa nenašla.")
    _conversation_for(db, current_user, msg.conversation_id)
    if msg.status != RUNNING or not await runner.stop(message_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Odpoveď už nebeží.")
    return {"stopping": True}


@router.post("/messages/{message_id}/new-version", response_model=PoradcaNewVersion)
def new_version_from_answer(
    message_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaNewVersion:
    """„Založiť novú verziu z tejto požiadavky" — koncept verzie z požiadavky v odpovedi Poradcu."""
    msg = db.get(PoradcaMessage, message_id)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Správa sa nenašla.")
    _conversation_for(db, current_user, msg.conversation_id)
    try:
        result = handoff.new_version_from_message(db, msg, user_id=current_user.id)
    except handoff.HandoffRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return PoradcaNewVersion(
        version_id=result.version_id,
        version_number=result.version_number,
        project_slug=result.project_slug,
        created=result.created,
    )


@router.websocket("/conversations/{conversation_id}/ws")
async def conversation_ws(websocket: WebSocket, conversation_id: uuid.UUID, token: str = Query(...)) -> None:
    """Živý priebeh rozhovoru: ``step`` (nástroj a cieľ), ``queued``, ``finished``. Obsah nikdy.

    Odmietnutie ako pri stavbe: zlý token pred ``accept()``, cudzí rozhovor po ňom s kódom 4003/4004.
    """
    db = SessionLocal()
    try:
        user = verify_ws_token(token, db)
        if user is None:
            await websocket.close(code=4003)
            return
        await websocket.accept()
        try:
            _conversation_for(db, user, conversation_id)
        except HTTPException as exc:
            await websocket.close(code=4004 if exc.status_code == 404 else 4003)
            return
    finally:
        db.close()

    queue = runner.hub.subscribe(conversation_id)

    async def _drain() -> None:
        try:
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return

    receiver = asyncio.ensure_future(_drain())
    try:
        while not receiver.done():
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await websocket.send_json(getter.result())
            else:
                getter.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        runner.hub.unsubscribe(conversation_id, queue)
