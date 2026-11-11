"""Poradca — conversations with an agent that only reads and advises (ICCINT-167, ``docs/specs/poradca.md``).

* ``GET   /status``                                   → whether Poradca can run (and why not)
* ``GET   /projects/{slug}/conversations``            → conversations of a project (mine; admin all)
* ``POST  /projects/{slug}/conversations``            → new conversation with a first question
* ``GET   /conversations/{id}``                       → conversation with messages
* ``PATCH /conversations/{id}``                       → what we are talking about (version / whole project)
* ``PUT   /conversations/{id}/title``                 → rename
* ``DELETE /conversations/{id}``                      → delete (text gone, cost stays in Costs)
* ``POST  /conversations/{id}/messages``              → next question
* ``POST  /messages/{id}/stop``                        → stop the answer
* ``POST  /messages/{id}/agent-tickets``               → attach the tickets the answer names for the agent (DEV-218)
* ``WS    /conversations/{id}/ws?token``               → live progress (steps, state)

Access: to a project its owner or an admin account (as everywhere in the cockpit); to a conversation its author or
admin. Anyone else gets 404 — a stranger's conversation must not even reveal that it exists. A deleted
conversation is 404 for everyone; its row stayed only because of Costs.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.routes.project_files import record_private_file
from backend.config.settings import settings
from backend.core import authz
from backend.core.offload import BlockingWorkTimedOut, run_blocking
from backend.core.security import get_current_user, require_shu_or_above, verify_ws_token
from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.poradca import AUTHOR_PORADCA, RUNNING, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal, get_db
from backend.schemas.poradca import (
    IdeaAddedCreate,
    IdeaAdditionRead,
    IdeaCreate,
    IdeaDetail,
    IdeaFoundedCreate,
    IdeaOutcome,
    IdeaProjectBriefRead,
    IdeaRead,
    PoradcaAgentTicketsAttached,
    PoradcaAsk,
    PoradcaBacklogSaved,
    PoradcaChoiceRead,
    PoradcaConversationCreate,
    PoradcaConversationDetail,
    PoradcaConversationRead,
    PoradcaMessageRead,
    PoradcaProjectContext,
    PoradcaRename,
    PoradcaReviewConversation,
    PoradcaScopeUpdate,
    PoradcaStatus,
    PoradcaTicketCreate,
    PoradcaTicketDraft,
    PoradcaTicketFounded,
    PoradcaTicketProposalRead,
    PoradcaVersionInfo,
)
from backend.services import claude_agent, metrics, plane_tickets, project_files
from backend.services import version as version_service
from backend.services.plane_gateway import PlaneError
from backend.services.poradca import (
    agent_tickets,
    attachments,
    block_language,
    choices,
    handoff,
    ideas,
    readiness,
    review,
    runner,
    tickets,
)
from backend.services.poradca.context import known_secret_values
from backend.services.poradca.secrets_filter import SecretFilter

router = APIRouter(tags=["Poradca"])

_TITLE_CHARS = 80
#: DEV-218 — reading up to five tickets from Plane (a few requests each, 30 s apiece at worst) and writing their files.
AGENT_TICKETS_CAP_SECONDS = 180


def _title(question: str) -> str:
    first = " ".join(question.strip().split())
    return first if len(first) <= _TITLE_CHARS else first[: _TITLE_CHARS - 1] + "…"


def _author_name(user: Optional[User]) -> str:
    if user is None:
        return "—"
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.username


def _check_version(db: Session, project: Optional[Project], version_id: Optional[uuid.UUID]) -> None:
    if version_id is None:
        return
    version = db.get(Version, version_id)
    if project is None or version is None or version.project_id != project.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Verzia nepatrí k tomuto projektu.")


def _conversation_for(
    db: Session, user: User, conversation_id: uuid.UUID
) -> tuple[PoradcaConversation, Optional[Project]]:
    """The conversation and its project — the author's or any for admin. A new idea (DEV-318) has no project yet:
    its author (or admin) is all the access it needs, ``None`` stands for the project."""
    conversation = db.get(PoradcaConversation, conversation_id)
    if (
        conversation is None
        or conversation.deleted_at is not None
        or not (conversation.author_id == user.id or authz.is_admin(user))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.")
    if conversation.project_id is None:
        return conversation, None
    project = authz.assert_project_id_access(db, user, conversation.project_id)
    return conversation, project


def _captured_backlog_number(db: Session, msg: PoradcaMessage) -> Optional[int]:
    if msg.captured_backlog_item_id is None:
        return None
    item = db.get(BacklogItem, msg.captured_backlog_item_id)
    return item.number if item is not None else None


def _ticket_proposal(content: str) -> Optional[PoradcaTicketProposalRead]:
    proposal = handoff.ticket_proposal(content)
    return PoradcaTicketProposalRead(kind=proposal.kind, title=proposal.title) if proposal else None


def _idea_project(content: str) -> Optional[IdeaProjectBriefRead]:
    brief = handoff.project_brief(content)
    return IdeaProjectBriefRead(**asdict(brief)) if brief else None


def _idea_addition(content: str) -> Optional[IdeaAdditionRead]:
    addition = handoff.project_addition(content)
    return IdeaAdditionRead(project_slug=addition.project_slug, title=addition.title) if addition else None


def _message_read(db: Session, msg: PoradcaMessage) -> PoradcaMessageRead:
    usage = msg.usage or {}
    done_answer = msg.author == AUTHOR_PORADCA and msg.status == "done"
    # DEV-264: an answer that asks the person offers nothing it prepared — that waits for the answer.
    held = done_answer and handoff.held_for_answer(msg.content)
    # DEV-356: what the person would pass on is not Slovak — nothing of it is offered, the screen says why.
    foreign = block_language.not_in_slovak(msg.content) if done_answer and not held else []
    offers = done_answer and not held and not foreign
    # ICCINT-168: by the same price list as Costs (tokens of all kinds × Claude Code price list × ECB rate).
    cost = metrics.usage_cost(db, usage, msg.created_at) if msg.author == AUTHOR_PORADCA and usage else None
    return PoradcaMessageRead(
        id=msg.id,
        author=msg.author,
        content=msg.content,
        steps=msg.steps or [],
        attachments=msg.attachments or [],
        status=msg.status,
        model=usage.get("model"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cost=cost,
        duration_seconds=msg.duration_seconds,
        error=msg.error,
        created_at=msg.created_at,
        finished_at=msg.finished_at,
        instruction=handoff.extract_block(msg.content, handoff.BLOCK_INSTRUCTION) if offers else None,
        backlog_request=handoff.backlog_request(msg.content) if offers else None,
        captured_backlog_number=_captured_backlog_number(db, msg),
        ticket_proposal=_ticket_proposal(msg.content) if offers else None,
        plane_identifier=msg.plane_identifier,
        plane_url=msg.plane_url,
        agent_tickets=handoff.agent_tickets(msg.content) if offers else [],
        held_for_answer=held,
        not_in_slovak=block_language.refusal(foreign) if foreign else None,
        idea_project=_idea_project(msg.content) if offers else None,
        idea_addition=_idea_addition(msg.content) if offers else None,
        choices=[
            PoradcaChoiceRead(card=c.card, label=c.label, status=c.status, question=c.question)
            for c in choices.choices(db, msg.content, msg.version_id)
        ]
        if done_answer
        else [],
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
        idea_id=conversation.idea_id,
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


def _accept_images(uploads: list) -> list[attachments.Incoming]:
    """DEV-52 — the pasted screenshots, checked before anything is created: refused → 422 with the sentence why."""
    try:
        return attachments.accept((u.name, u.data) for u in uploads)
    except attachments.AttachmentRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _ask(
    db: Session,
    conversation: PoradcaConversation,
    question: str,
    user: User,
    images: Optional[list[attachments.Incoming]] = None,
) -> None:
    ready = readiness.status(db)
    if not ready.ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{runner.agent_of(conversation).name} teraz nevie bežať: " + "; ".join(ready.problems),
        )
    try:
        runner.ask(db, conversation, question, user, images or [])
    except runner.PoradcaBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except runner.ConversationGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.") from exc


def _edit(db: Session, conversation: PoradcaConversation, **values: object) -> None:
    try:
        runner.edit_conversation(db, conversation, **values)
    except runner.ConversationGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rozhovor sa nenašiel.") from exc


# ── DEV-318 Nápady: a new idea talked over before any project exists ─────────────────────────────────────────────────


def _idea_read(db: Session, idea, conversation: PoradcaConversation) -> IdeaRead:
    project = ideas.outcome_project(db, idea)
    read = _conversation_read(db, conversation)
    return IdeaRead(
        id=idea.id,
        title=conversation.title,
        state=ideas.state(db, idea, conversation),
        conversation_id=conversation.id,
        author_name=read.author_name,
        running=read.running,
        cost=ideas.cost(db, conversation.id),
        outcome_project_slug=project.slug if project else None,
        outcome_project_name=project.name if project else None,
        outcome_backlog_number=ideas.outcome_backlog_number(db, idea),
        talk_in_project=conversation.project_id is not None,
        plane_identifier=idea.plane_identifier,
        plane_url=idea.plane_url,
        created_at=idea.created_at,
        updated_at=conversation.updated_at,
    )


def _idea_for(db: Session, user: User, idea_id: uuid.UUID):
    try:
        return ideas.get_for(db, user, idea_id)
    except ideas.IdeaGone as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Nápad sa nenašiel.") from exc


@router.get("/ideas", response_model=list[IdeaRead])
def list_ideas(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[IdeaRead]:
    """The person's ideas (all for the admin account), the most recently talked first."""
    return [_idea_read(db, idea, conversation) for idea, conversation in ideas.visible(db, current_user)]


@router.post("/ideas", response_model=IdeaDetail, status_code=status.HTTP_201_CREATED)
async def create_idea(
    payload: IdeaCreate, db: Session = Depends(get_db), current_user: User = Depends(require_shu_or_above)
) -> IdeaDetail:
    """A new idea: its conversation with Poradca starts with the rough idea. Whoever may found a project may start
    one (the same role as Nový projekt)."""
    images = _accept_images(payload.attachments)
    idea, conversation = ideas.create(
        db, author=current_user, title=_title((payload.title or "").strip() or payload.question)
    )
    db.commit()
    _ask(db, conversation, payload.question, current_user, images)
    return IdeaDetail(**_idea_read(db, idea, conversation).model_dump(), conversation=_detail(db, conversation))


@router.get("/ideas/{idea_id}", response_model=IdeaDetail)
def get_idea(
    idea_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> IdeaDetail:
    idea, conversation = _idea_for(db, current_user, idea_id)
    return IdeaDetail(**_idea_read(db, idea, conversation).model_dump(), conversation=_detail(db, conversation))


@router.post("/ideas/{idea_id}/founded", response_model=IdeaOutcome)
def idea_founded(
    idea_id: uuid.UUID,
    payload: IdeaFoundedCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> IdeaOutcome:
    """The person founded a project from the idea's brief: its 0.1.0 gets the brief, the talk moves to it, and the
    project gets its ticket in the DEV register. A second click returns the same — and files a ticket that failed."""
    idea, conversation = _idea_for(db, current_user, idea_id)
    project = authz.assert_project_id_access(db, current_user, payload.project_id)
    try:
        idea = ideas.found(db, idea, conversation, message_id=payload.message_id, project=project)
        db.commit()
    except ideas.OutcomeRefused as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except version_service.ZadanieWouldBeOverwritten as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Verzia 0.1.0 projektu už má iné zadanie — zadanie z nápadu ho neprepíše.",
        ) from exc
    ticket_error = None
    try:
        ideas.file_project_ticket(db, plane_tickets.gateway(), current_user, idea, conversation, project)
        db.commit()
    except PlaneError as exc:
        db.rollback()
        ticket_error = str(exc)
    db.refresh(idea)
    return IdeaOutcome(**_idea_read(db, idea, conversation).model_dump(), ticket_error=ticket_error)


@router.post("/ideas/{idea_id}/added", response_model=IdeaOutcome)
def idea_added(
    idea_id: uuid.UUID,
    payload: IdeaAddedCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IdeaOutcome:
    """The idea's brief goes to the Zásobník of the existing project it names — any project of the cockpit (DEV-350);
    the talk moves to that project when its author may open it, otherwise it stays with him in Nápady."""
    idea, conversation = _idea_for(db, current_user, idea_id)
    try:
        slug = ideas.addition_target(db, conversation, payload.message_id)
        project = db.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Projekt sa nenašiel.")
        may_open = authz.is_owner_or_admin(current_user, authz.project_owner_id(project))
        idea = ideas.add(db, idea, conversation, message_id=payload.message_id, project=project, talk_moves=may_open)
        db.commit()
    except ideas.OutcomeRefused as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.refresh(idea)
    return IdeaOutcome(**_idea_read(db, idea, conversation).model_dump())


@router.delete("/ideas/{idea_id}", status_code=status.HTTP_204_NO_CONTENT)
async def throw_away_idea(
    idea_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Response:
    """Delete an idea from Nápady — one that ended nowhere with its talk (the cost of its answers stays), one that ended
    in a project from the list only: its talk stays in the project's Poradca (DEV-331)."""
    idea, conversation = _idea_for(db, current_user, idea_id)
    try:
        await ideas.throw_away(db, idea, conversation)
        db.commit()
    except runner.PoradcaBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/status", response_model=PoradcaStatus)
def poradca_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> PoradcaStatus:
    return readiness.status(db)


def _instruction_target(state: Optional[PipelineState]) -> tuple[bool, Optional[str]]:
    """Whether the Control Centre field of this version accepts an instruction — same rules as for the field itself."""
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
    """A project and its versions with the phase and build state — for the „what are we talking about" choice."""
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
    """My conversations of a project, newest first. Admin sees everyone's conversations."""
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
    images = _accept_images(payload.attachments)
    # DEV-138: a card from the „Overiť s Poradcom“ button founds the conversation for the cards of this version in
    # its current phase — the next card finds it through ``review-conversation``. A version never built has no phase:
    # its card founds an ordinary conversation.
    stage = review.stage_of(db, payload.version_id) if payload.review and payload.version_id else None
    title = (
        review.title(db.get(Version, payload.version_id).version_number, stage) if stage else _title(payload.question)
    )
    conversation = runner.new_conversation(
        db, project=project, author=current_user, version_id=payload.version_id, title=title
    )
    conversation.review_stage = stage
    db.commit()
    _ask(db, conversation, payload.question, current_user, images)
    return _detail(db, conversation)


@router.get("/projects/{slug}/review-conversation", response_model=PoradcaReviewConversation)
def review_conversation(
    slug: str,
    version_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaReviewConversation:
    """DEV-138 — where the „Overiť s Poradcom“ button sends a card: the person's own conversation for the cards of
    this version in its current phase, or ``None`` when the card is to found it."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    _check_version(db, project, version_id)
    return PoradcaReviewConversation(
        conversation_id=review.find(db, project_id=project.id, author_id=current_user.id, version_id=version_id)
    )


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
    """Rename a conversation. The order in the list does not change — it is determined by the last question."""
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    _edit(db, conversation, title=payload.title)
    return _conversation_read(db, conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_conversation(
    conversation_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Response:
    """Delete a conversation: questions, answers, steps and the log on disk are gone for good; the cost stays in Costs.

    While Poradca is answering, 409 — the answer must be stopped first.
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
    _ask(db, conversation, payload.question, current_user, _accept_images(payload.attachments))
    return _detail(db, conversation)


@router.get("/conversations/{conversation_id}/attachments/{attachment_id}", response_class=FileResponse)
def get_attachment(
    conversation_id: uuid.UUID,
    attachment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """DEV-52 — one screenshot of a question, for whoever may read the conversation (anyone else gets 404).

    Found through the conversation's own messages, never by a path from the request — another conversation's
    image is not reachable even with its id."""
    conversation, _project = _conversation_for(db, current_user, conversation_id)
    for (stored,) in db.execute(
        select(PoradcaMessage.attachments).where(PoradcaMessage.conversation_id == conversation.id)
    ).all():
        meta = next((m for m in stored or [] if m.get("id") == attachment_id), None)
        path = attachments.path_of(conversation.id, meta) if meta else None
        if path is not None and path.is_file():
            return FileResponse(path, media_type=meta["mime"], headers={"Cache-Control": "private, max-age=3600"})
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Obrázok sa nenašiel.")


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


@router.post("/messages/{message_id}/backlog", response_model=PoradcaBacklogSaved)
def save_request_to_backlog(
    message_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaBacklogSaved:
    """„Uložiť do Zásobníka" — požiadavka z odpovede Poradcu do Zásobníka projektu; verzia nevzniká (DEV-29)."""
    msg = db.get(PoradcaMessage, message_id)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Správa sa nenašla.")
    _conversation_for(db, current_user, msg.conversation_id)
    try:
        result = handoff.backlog_item_from_message(db, msg)
    except handoff.HandoffRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return PoradcaBacklogSaved(
        backlog_item_id=result.backlog_item_id,
        number=result.number,
        project_slug=result.project_slug,
        created=result.created,
    )


def _answer_for(db: Session, user: User, message_id: uuid.UUID) -> PoradcaMessage:
    msg = db.get(PoradcaMessage, message_id)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Správa sa nenašla.")
    _conversation_for(db, user, msg.conversation_id)
    return msg


@router.get("/messages/{message_id}/plane-ticket", response_model=PoradcaTicketDraft)
def plane_ticket_draft(
    message_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaTicketDraft:
    """DEV-119 — the preview „Založiť tiket" opens with: the proposal, similar open tickets, what the cockpit adds."""
    msg = _answer_for(db, current_user, message_id)
    try:
        found = tickets.draft(db, plane_tickets.gateway(), msg, current_user)
    except handoff.HandoffRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PlaneError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PoradcaTicketDraft(**asdict(found))


@router.post("/messages/{message_id}/plane-ticket", response_model=PoradcaTicketFounded)
def found_plane_ticket(
    message_id: uuid.UUID,
    payload: PoradcaTicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PoradcaTicketFounded:
    """DEV-119 — „Založiť tiket": the edited proposal becomes a ticket in Plane, once; nothing else starts."""
    _answer_for(db, current_user, message_id)
    try:
        msg, created = tickets.found(db, plane_tickets.gateway(), message_id, current_user, **payload.model_dump())
        db.commit()
    except handoff.HandoffRefused as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PoradcaTicketFounded(plane_identifier=msg.plane_identifier, plane_url=msg.plane_url, created=created)


@router.post("/messages/{message_id}/agent-tickets", response_model=PoradcaAgentTicketsAttached)
async def attach_agent_tickets(
    message_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PoradcaAgentTicketsAttached:
    """DEV-218 — „Priložiť k odpovedi agentovi": the tickets the answer names, read from Plane now, become files in
    ``private/`` of the build's project; the lines for the answer say where. Nothing is sent to the agent."""
    msg = _answer_for(db, current_user, message_id)
    try:
        where = agent_tickets.target(db, msg)
    except handoff.HandoffRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    authz.assert_version_access(db, current_user, where.version.id)
    secret_filter = SecretFilter(known_secret_values(db, where.project))
    try:
        attached = await run_blocking(
            agent_tickets.attach,
            plane_tickets.gateway(),
            claude_agent.PROJECTS_ROOT / where.project.slug,
            where.identifiers,
            secret_filter,
            max_bytes=settings.private_file_max_bytes,
            cap=AGENT_TICKETS_CAP_SECONDS,
        )
    except PlaneError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except project_files.PrivateFileRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BlockingWorkTimedOut as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plane neodpovedal včas — skús to znova o chvíľu."
        ) from exc
    for a in attached:
        size = project_files.human_size(a.stored.size_bytes)
        await record_private_file(
            db,
            where.version.id,
            f"{current_user.username} priložil tiket {a.identifier} z Plane ako súbor {a.stored.path} ({size}) — "
            "leží v projekte mimo gitu.",
            {
                "action": "uploaded",
                "path": a.stored.path,
                "size_bytes": a.stored.size_bytes,
                "by": current_user.username,
                "plane_ticket": a.identifier,
            },
        )
    return PoradcaAgentTicketsAttached(
        attached=[a.stored.path for a in attached], answer_text=agent_tickets.answer_text(attached)
    )


@router.websocket("/conversations/{conversation_id}/ws")
async def conversation_ws(websocket: WebSocket, conversation_id: uuid.UUID, token: str = Query(...)) -> None:
    """Live progress of a conversation: ``step`` (tool and target), ``queued``, ``finished``. Never the content.

    Rejection as in the build: bad token before ``accept()``, a stranger's conversation after it with code 4003/4004.
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
