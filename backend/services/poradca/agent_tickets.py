"""The tickets the agent asked for, attached to its answer as files — read from Plane at the click (DEV-218).

24.10.2026 the brief of NEX Knowledge 0.1.0 named its design and its plan of versions without their content: the design
is ticket MAGER-66, the plan a comment of DEV-186. The agent's turn does not see Plane, so it asked for both and the
Director sent them in an answer. Director: „ak Agent sa narazí na tiket (napríklad MAGER-66) bez priloženého obsahu tak
požiadá (tlačítko ako to už máme v iných mistach) Poradcu aby doplnil chýbajúce informácie.“

So the agent names the ticket in its question, the Manažér asks Poradca („Overiť s Poradcom“), Poradca reads it
(tool ``plane_tiket``) and puts its number into ``<tiket-pre-agenta>``. Only tickets of DEV — Director 24.10.2026:
„Úplne všetko čo sa týka vývojových prác majú by zachytené v tiketoch DEV. Preto v zadaní nikde nemá byť iný tiket
uvedený ako DEV.“ (MAGER-66 was then taken over into DEV-186.) „Priložiť k odpovedi agentovi“ under the answer
lands here: the tickets the STORED answer names — never what the browser sends — are read from Plane at that moment,
not retyped by Poradca, each into ``private/<identifier>.md`` (git never sees it, :mod:`backend.services.project_files`)
through the same secret filter as Poradca's tools. Every ticket is read before any file is written: half of them is not
an answer. Attached again, a ticket replaces its file — the agent never has two that differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.poradca import AUTHOR_PORADCA, DONE, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import plane_tickets, project_files
from backend.services.plane_gateway import PlaneError, PlaneGateway, ticket_identifier
from backend.services.poradca.handoff import HandoffRefused, agent_tickets
from backend.services.poradca.secrets_filter import SecretFilter


@dataclass(frozen=True)
class AttachedTicket:
    identifier: str
    title: str
    stored: project_files.PrivateFile

    @property
    def answer_line(self) -> str:
        return project_files.answer_line(self.stored, label=f"Obsah tiketu {self.identifier} z Plane ({self.title})")


@dataclass(frozen=True)
class Target:
    """Where the tickets of an answer go: the build of the conversation's version, in its project's folder."""

    project: Project
    version: Version
    identifiers: list[str]


def target(db: Session, message: PoradcaMessage) -> Target:
    """The build and the tickets a finished Poradca answer attaches — else :class:`HandoffRefused` with the sentence."""
    if message.author != AUTHOR_PORADCA or message.status != DONE:
        raise HandoffRefused("Tiket sa dá priložiť len z hotovej odpovede Poradcu.")
    identifiers = agent_tickets(message.content)
    if not identifiers:
        raise HandoffRefused("V tejto odpovedi nie je tiket pre agenta.")
    try:
        identifiers = [ticket_identifier(i, settings.plane_dev_project) for i in identifiers]
    except PlaneError as exc:
        raise HandoffRefused(str(exc)) from exc
    conversation = db.get(PoradcaConversation, message.conversation_id)
    version = db.get(Version, conversation.version_id) if conversation and conversation.version_id else None
    if version is None:
        raise HandoffRefused("Rozhovor je o celom projekte — hore vyber verziu, do ktorej stavby tiket patrí.")
    project = db.get(Project, version.project_id)
    if project is None:
        raise HandoffRefused("Projekt rozhovoru už neexistuje.")
    return Target(project=project, version=version, identifiers=identifiers)


def attach(
    gateway: PlaneGateway,
    project_root: Path,
    identifiers: list[str],
    secret_filter: SecretFilter,
    *,
    max_bytes: int,
) -> list[AttachedTicket]:
    """Read every ticket from Plane, then store each as ``private/<identifier>.md`` — blocking, run it off the loop.

    Raises :class:`PlaneError` (nothing is written then) or :class:`project_files.PrivateFileRefused`."""
    tickets = [plane_tickets.read_ticket(gateway, identifier) for identifier in identifiers]
    attached = []
    for ticket in tickets:
        content = secret_filter(ticket.document()).encode("utf-8")
        stored = project_files.save(project_root, f"{ticket.identifier}.md", content, max_bytes=max_bytes, replace=True)
        attached.append(AttachedTicket(ticket.identifier, ticket.title, stored))
    return attached


def answer_text(attached: list[AttachedTicket]) -> str:
    """The lines for the answer to the agent — one per ticket, where its file lies."""
    return "\n".join(a.answer_line for a in attached)
