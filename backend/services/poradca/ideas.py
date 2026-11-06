"""Nápady — new ideas talked over with Poradca before any project exists (DEV-318, ``docs/specs/ideas.md``).

An idea is a row of ``ideas`` and its one Poradca conversation (``poradca_conversations.idea_id``), which has no
project until the idea's outcome gives it one. What the list shows as the idea's state is derived here and nowhere
else: an outcome → ``founded`` / ``added``; an answer whose brief the person can act on → ``brief_ready``;
otherwise ``open``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.idea import OUTCOME_ADDED, OUTCOME_FOUNDED, Idea
from backend.db.models.poradca import AUTHOR_PORADCA, DONE, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service
from backend.services import backlog_tickets, metrics, plane_tickets
from backend.services import version as version_service
from backend.services.plane_gateway import PlaneError, PlaneGateway
from backend.services.poradca import handoff, runner

STATE_OPEN = "open"
STATE_BRIEF_READY = "brief_ready"
STATE_FOUNDED = OUTCOME_FOUNDED
STATE_ADDED = OUTCOME_ADDED

#: Why a concluded idea cannot be thrown away — its conversation is the project's now.
CONCLUDED = "Nápad už skončil v projekte — jeho rozhovor patrí projektu; zmazať ho môžeš v Poradcovi projektu."


#: The version a project founded from an idea gets its brief in — the one the cockpit founds with every new project.
FIRST_VERSION = "0.1.0"

NO_BRIEF = "V nápade ešte nie je zadanie — Poradca ho napíše, keď je všetko jasné."
REWRITTEN = "Toto zadanie Poradca neskôr prepísal — použi to posledné, pod jeho poslednou odpoveďou."
NOT_A_NEW_PROJECT = "Posledné zadanie dopĺňa existujúci projekt — tlačidlo pod ním ho pridá do jeho Zásobníka."
NOT_AN_ADDITION = "Posledné zadanie je na nový projekt — tlačidlo pod ním otvorí formulár Nový projekt."
NO_WORK_KIND = (
    "V zadaní chýba, kto prvú verziu platí (zákazník, alebo je to dar ICC) — povedz to Poradcovi a on zadanie doplní."
)
NOT_NEW = (
    f"Projekt už nemá nezačatú verziu {FIRST_VERSION} — zadanie z nápadu patrí len do projektu, ktorý si z neho práve "
    "založil."
)


class IdeaGone(LookupError):
    """No such idea for this person (or it was thrown away)."""


class OutcomeRefused(ValueError):
    """The idea cannot end this way now — a sentence for the person."""


def create(db: Session, *, author: User, title: str) -> tuple[Idea, PoradcaConversation]:
    """A new idea and its conversation — no project, no version."""
    idea = Idea(author_id=author.id)
    db.add(idea)
    db.flush()
    conversation = runner.new_conversation(db, idea_id=idea.id, author=author, version_id=None, title=title)
    return idea, conversation


def get_for(db: Session, user: User, idea_id: UUID) -> tuple[Idea, PoradcaConversation]:
    """The idea and its conversation — the author's, or any for the admin account; thrown away ones do not exist."""
    idea = db.get(Idea, idea_id)
    if idea is None or idea.deleted_at is not None or not (idea.author_id == user.id or authz.is_admin(user)):
        raise IdeaGone(idea_id)
    conversation = conversation_of(db, idea)
    if conversation is None:
        raise IdeaGone(idea_id)
    return idea, conversation


def conversation_of(db: Session, idea: Idea) -> Optional[PoradcaConversation]:
    return db.execute(
        select(PoradcaConversation).where(
            PoradcaConversation.idea_id == idea.id, PoradcaConversation.deleted_at.is_(None)
        )
    ).scalar_one_or_none()


def visible(db: Session, user: User) -> list[tuple[Idea, PoradcaConversation]]:
    """The ideas the person sees — their own, all for the admin account — the most recently talked first."""
    query = (
        select(Idea, PoradcaConversation)
        .join(PoradcaConversation, PoradcaConversation.idea_id == Idea.id)
        .where(Idea.deleted_at.is_(None), PoradcaConversation.deleted_at.is_(None))
        .order_by(PoradcaConversation.updated_at.desc())
    )
    if not authz.is_admin(user):
        query = query.where(Idea.author_id == user.id)
    return [(idea, conversation) for idea, conversation in db.execute(query).all()]


def _answers(db: Session, conversation_id: UUID) -> list[PoradcaMessage]:
    return list(
        db.execute(
            select(PoradcaMessage)
            .where(PoradcaMessage.conversation_id == conversation_id, PoradcaMessage.author == AUTHOR_PORADCA)
            .order_by(PoradcaMessage.seq.asc())
        ).scalars()
    )


def offers_outcome(message: PoradcaMessage) -> bool:
    """A finished answer whose brief the person can act on — not one that is still asking them (DEV-264)."""
    if message.author != AUTHOR_PORADCA or message.status != DONE or handoff.held_for_answer(message.content):
        return False
    return handoff.project_brief(message.content) is not None or handoff.project_addition(message.content) is not None


def latest_brief(db: Session, conversation_id: UUID) -> Optional[PoradcaMessage]:
    """The last answer with a brief — what the outcome buttons take (an older brief was rewritten by a newer one)."""
    briefs = [m for m in _answers(db, conversation_id) if offers_outcome(m)]
    return briefs[-1] if briefs else None


def state(db: Session, idea: Idea, conversation: PoradcaConversation) -> str:
    if idea.outcome in (STATE_FOUNDED, STATE_ADDED):
        return idea.outcome
    return STATE_BRIEF_READY if latest_brief(db, conversation.id) is not None else STATE_OPEN


def cost(db: Session, conversation_id: UUID) -> Optional[float]:
    """What the idea's answers cost, by the price list of Náklady; ``None`` before any priced answer."""
    prices = [metrics.usage_cost(db, m.usage, m.created_at) for m in _answers(db, conversation_id) if m.usage]
    known = [p for p in prices if p is not None]
    return round(sum(known), 2) if known else None


def outcome_project(db: Session, idea: Idea) -> Optional[Project]:
    return db.get(Project, idea.outcome_project_id) if idea.outcome_project_id else None


def outcome_backlog_number(db: Session, idea: Idea) -> Optional[int]:
    item = db.get(BacklogItem, idea.outcome_backlog_item_id) if idea.outcome_backlog_item_id else None
    return item.number if item is not None else None


def _locked(db: Session, idea: Idea) -> Idea:
    """The idea's row, locked — a second click waits and then finds the outcome the first one wrote."""
    return db.execute(
        select(Idea).where(Idea.id == idea.id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()


def _brief_message(db: Session, conversation: PoradcaConversation, message_id: UUID) -> PoradcaMessage:
    latest = latest_brief(db, conversation.id)
    if latest is None:
        raise OutcomeRefused(NO_BRIEF)
    if latest.id != message_id:
        raise OutcomeRefused(REWRITTEN)
    return latest


def _ended_elsewhere(idea: Idea) -> OutcomeRefused:
    return OutcomeRefused("Nápad už skončil — pozri, kam, v zozname Nápadov.")


def _conclude(
    idea: Idea, conversation: PoradcaConversation, outcome: str, project: Project, message: PoradcaMessage
) -> None:
    idea.outcome = outcome
    idea.outcome_project_id = project.id
    idea.outcome_message_id = message.id
    idea.concluded_at = datetime.now(timezone.utc)
    # The talk moves to the project and goes on there, with the project's rules from its next question (DEV-30).
    conversation.project_id = project.id


def found(db: Session, idea: Idea, conversation: PoradcaConversation, *, message_id: UUID, project: Project) -> Idea:
    """The person founded ``project`` from the brief (the form Nový projekt, prefilled from it): the brief becomes the
    Zadanie of its 0.1.0, as a Dedo's brief does (``customer-requirements.md`` and the version's description), and the
    talk moves to the project. A second click returns what the first one did.

    Raises:
        OutcomeRefused: no brief, a rewritten one, an addition, no work kind, or a project that is not new.
        version_service.ZadanieWouldBeOverwritten: the version already has another Zadanie on disk (ICCINT-71).
    """
    idea = _locked(db, idea)
    if idea.outcome == OUTCOME_FOUNDED and idea.outcome_project_id == project.id:
        return idea
    if idea.outcome is not None:
        raise _ended_elsewhere(idea)
    message = _brief_message(db, conversation, message_id)
    brief = handoff.project_brief(message.content)
    if brief is None:
        raise OutcomeRefused(NOT_A_NEW_PROJECT)
    if brief.work_kind is None:
        raise OutcomeRefused(NO_WORK_KIND)
    version = version_service.unstarted_version(db, project.id, FIRST_VERSION)
    if version is None:
        raise OutcomeRefused(NOT_NEW)
    version.description = brief.brief
    db.flush()
    version_service.write_zadanie(db, version.id, brief.brief)
    _conclude(idea, conversation, OUTCOME_FOUNDED, project, message)
    db.flush()
    return idea


def file_project_ticket(
    db: Session, plane: PlaneGateway, user: User, idea: Idea, conversation: PoradcaConversation, project: Project
) -> None:
    """The founded project's ticket in the DEV register, with the brief — once. The cockpit founds the project's
    module with it (``plane_tickets.module_for``). A failure leaves the idea founded and the ticket for a second try.

    Raises:
        PlaneError: Plane refused, or the person is not in the „Zodpovedný“ group of the DEV register.
    """
    if idea.plane_issue_id:
        return
    message = db.get(PoradcaMessage, idea.outcome_message_id) if idea.outcome_message_id else None
    brief = handoff.project_brief(message.content) if message is not None else None
    if brief is None or brief.work_kind is None:
        raise PlaneError("Zadanie, z ktorého projekt vznikol, sa nenašlo — tiket sa nedá založiť.")
    responsible = backlog_tickets.responsible_of(user, backlog_tickets.responsible_people(plane))
    if responsible is None:
        raise PlaneError(
            f"Teba evidencia DEV v skupine „{backlog_tickets.RESPONSIBLE_GROUP}“ nemá — tiket projektu založí Andros "
            "Dedo, keď ťa do nej pridá."
        )
    ref = plane_tickets.file_ticket(
        db,
        plane,
        project=project,
        user=user,
        source="ideas",
        title=f"{project.name} ({project.slug}): zadanie prvej verzie {FIRST_VERSION}",
        description=(
            f"{brief.brief}\n\nZadanie vzniklo v Nápadoch kokpitu („{conversation.title}“) a je zapísané vo verzii "
            f"{FIRST_VERSION} projektu."
        ),
        labels=(f"{backlog_tickets.TICKET_TYPE_GROUP}: Task", f"{backlog_tickets.RESPONSIBLE_GROUP}: {responsible}"),
        doer=responsible,
        work_kind=brief.work_kind,
    )
    idea.plane_issue_id, idea.plane_identifier, idea.plane_url = ref.id, ref.identifier, ref.url
    db.flush()


def addition_target(db: Session, conversation: PoradcaConversation, message_id: UUID) -> str:
    """The slug of the project the latest brief is for — the route checks the person's access to it."""
    addition = handoff.project_addition(_brief_message(db, conversation, message_id).content)
    if addition is None:
        raise OutcomeRefused(NOT_AN_ADDITION)
    return addition.project_slug


def add(db: Session, idea: Idea, conversation: PoradcaConversation, *, message_id: UUID, project: Project) -> Idea:
    """The brief goes to ``project``'s Zásobník as a requirement — which version takes it, the Director decides
    (DEV-29) — and the talk moves to the project. A second click returns what the first one did.

    Raises:
        OutcomeRefused: no brief, a rewritten one, a new project's brief, or a brief for another project.
    """
    idea = _locked(db, idea)
    if idea.outcome == OUTCOME_ADDED and idea.outcome_project_id == project.id:
        return idea
    if idea.outcome is not None:
        raise _ended_elsewhere(idea)
    message = _brief_message(db, conversation, message_id)
    addition = handoff.project_addition(message.content)
    if addition is None:
        raise OutcomeRefused(NOT_AN_ADDITION)
    if addition.project_slug != project.slug:
        raise OutcomeRefused(f"Zadanie je pre projekt {addition.project_slug}, nie pre {project.slug}.")
    item = backlog_service.create(
        db, BacklogItemCreate(project_id=project.id, title=addition.title, description=addition.brief)
    )
    _conclude(idea, conversation, OUTCOME_ADDED, project, message)
    idea.outcome_backlog_item_id = item.id
    db.flush()
    return idea


async def throw_away(db: Session, idea: Idea, conversation: PoradcaConversation) -> None:
    """The author throws the idea away: its talk is wiped as a deleted conversation's is, the cost stays.

    Raises:
        ValueError: the idea already ended in a project (:data:`CONCLUDED`).
        runner.PoradcaBusy: an answer is still running.
    """
    if idea.outcome is not None:
        raise ValueError(CONCLUDED)
    await runner.delete_conversation(db, conversation)
    idea.deleted_at = datetime.now(timezone.utc)
