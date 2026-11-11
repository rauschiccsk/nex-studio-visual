"""Poradca schemas (ICCINT-167)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.backlog import TicketType
from backend.schemas.delivery_statement import WorkKind

#: Longest question — longer text is more likely a pasted log; Poradca reads that itself with a tool.
MAX_QUESTION_CHARS = 20_000
#: Longest conversation title — width of the ``poradca_conversations.title`` column.
TITLE_MAX_CHARS = 200


def _refuse_nul(value: str) -> str:
    """A NUL character is not stored by the database in text (500 crash) — rejected with a sentence, not an error."""
    if "\x00" in value:
        raise ValueError("Text nesmie obsahovať znak NUL.")
    return value


class PoradcaStep(BaseModel):
    """What Poradca did — tool and target, never content."""

    tool: str
    target: str = ""


class PoradcaAttachmentUpload(BaseModel):
    """DEV-52 — snímka obrazovky priložená k otázke: meno súboru a obsah v base64 (tak ako ich berie aj API
    Claude). Typ sa neberie z mena ani z prehliadača — backend ho rozpozná z obsahu."""

    name: str = Field(default="", max_length=500)
    data: str = Field(..., min_length=1)


class PoradcaAttachmentRead(BaseModel):
    """Priložená snímka — čo o nej vie obrazovka (obsah sa sťahuje zvlášť, s overením prístupu)."""

    id: str
    name: str
    mime: str
    size_bytes: int


class PoradcaChoiceRead(BaseModel):
    """DEV-83 — a choice Poradca marked in its answer, checked against the Decision Card it names."""

    card: Optional[int] = None
    label: str
    #: ``on_card`` the card offers exactly this option; ``not_on_card`` it does not (or the card is gone);
    #: ``answer`` no card — the answer Poradca suggests for the agent's question.
    status: Literal["on_card", "not_on_card", "answer"]
    #: The card's question, when the card exists.
    question: Optional[str] = None


class IdeaProjectBriefRead(BaseModel):
    """DEV-318 — a new project's brief in an idea's answer: what the form Nový projekt is prefilled with. A field
    Poradca did not name (or named so the cockpit cannot read it) is ``None`` and the form keeps its default."""

    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    auth_mode: Optional[str] = None
    #: ``None`` — not named; ``""`` — no template on purpose.
    template: Optional[str] = None
    ai: Optional[bool] = None
    #: ``change`` or ``gift`` — who pays the first version; without it the project is not founded from the idea.
    work_kind: Optional[str] = None
    brief: str


class IdeaAdditionRead(BaseModel):
    """DEV-318 — an idea's brief for an existing project: it goes to that project's Zásobník."""

    project_slug: str
    title: str


class PoradcaMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author: Literal["human", "poradca"]
    content: str
    steps: list[PoradcaStep] = Field(default_factory=list)
    #: DEV-52 — snímky, ktoré Manažér priložil k otázke.
    attachments: list[PoradcaAttachmentRead] = Field(default_factory=list)
    status: Literal["running", "done", "failed", "stopped"]
    #: Full model name as reported by Claude Code; ``None`` for a question and for an unfinished answer.
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    #: Cost of the answer in EUR by the same path as the build; ``None`` when the model has no price set.
    cost: Optional[float] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    #: The answer carries an instruction for the agent of a running build (``<pokyn-pre-agenta>`` block) — to insert.
    instruction: Optional[str] = None
    #: Odpoveď nesie požiadavku do Zásobníka (blok ``<poziadavka-do-zasobnika>``, staršie odpovede
    #: ``<poziadavka-na-novu-verziu>``) — DEV-29.
    backlog_request: Optional[str] = None
    #: Číslo požiadavky (REQ-N), ktorú človek z tejto odpovede už uložil do Zásobníka.
    captured_backlog_number: Optional[int] = None
    #: DEV-83 — the choices the answer marks (``<volba>``), in its order; empty for a question or unfinished answer.
    choices: list[PoradcaChoiceRead] = Field(default_factory=list)
    #: DEV-119 — the ticket the answer proposes (``<tiket-nsv>`` / ``<tiket-projektu>``) and the one founded from it.
    ticket_proposal: Optional[PoradcaTicketProposalRead] = None
    plane_identifier: Optional[str] = None
    plane_url: Optional[str] = None
    #: DEV-218 — the tickets the answer attaches for the agent (``<tiket-pre-agenta>``), as Plane writes them.
    agent_tickets: list[str] = Field(default_factory=list)
    #: DEV-264 — the answer asks the person and prepared something anyway: nothing is offered until the answer.
    held_for_answer: bool = False
    #: DEV-356 — what the person would pass on (an instruction, an answer for the agent, a requirement, a brief) is not
    #: Slovak: nothing of it is offered, and this is the sentence why; ``None`` when it is Slovak or there is none.
    not_in_slovak: Optional[str] = None
    #: DEV-318 — the idea's two outcomes this answer offers (only in a finished answer that asks nothing).
    idea_project: Optional[IdeaProjectBriefRead] = None
    idea_addition: Optional[IdeaAdditionRead] = None


class PoradcaAgentTicketsAttached(BaseModel):
    """DEV-218 — „Priložiť k odpovedi agentovi": each ticket's content read from Plane, stored in ``private/``."""

    attached: list[str]
    #: The lines the screen puts into the answer for the agent — where each ticket's file lies.
    answer_text: str


class PoradcaBacklogSaved(BaseModel):
    """DEV-29: požiadavka z odpovede Poradcu uložená do Zásobníka (verzia z nej nevzniká)."""

    backlog_item_id: UUID
    number: int
    project_slug: str
    created: bool


class PoradcaConversationRead(BaseModel):
    id: UUID
    #: ``None`` while the conversation is a new idea that belongs to no project yet (DEV-318, ``idea_id``).
    project_id: Optional[UUID] = None
    #: DEV-318 — the idea (Nápady) this conversation is the talk of; it stays after the idea ended, as its origin.
    idea_id: Optional[UUID] = None
    #: What we are talking about — ``None`` = the whole project.
    version_id: Optional[UUID] = None
    version_number: Optional[str] = None
    author_id: UUID
    author_name: str
    title: str
    #: The answer is running now.
    running: bool = False
    created_at: datetime
    updated_at: datetime


class PoradcaConversationDetail(PoradcaConversationRead):
    messages: list[PoradcaMessageRead] = Field(default_factory=list)


class PoradcaConversationCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    version_id: Optional[UUID] = None
    #: DEV-52 — snímky obrazovky k prvej otázke (počet a veľkosť stráži ``poradca.attachments.accept``).
    attachments: list[PoradcaAttachmentUpload] = Field(default_factory=list)
    #: DEV-138 — founded by the „Overiť s Poradcom“ button: the conversation is the one for the cards of this version
    #: in its current phase, titled „<verzia> — rozhodnutia v <fáze>“ (``backend.services.poradca.review``).
    review: bool = False

    @field_validator("question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        return _refuse_nul(value)


class PoradcaReviewConversation(BaseModel):
    """DEV-138 — the person's conversation for the cards of a version in its current phase; ``None`` = found one."""

    conversation_id: Optional[UUID] = None


class PoradcaAsk(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    #: DEV-52 — snímky obrazovky k otázke (počet a veľkosť stráži ``poradca.attachments.accept``).
    attachments: list[PoradcaAttachmentUpload] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        return _refuse_nul(value)


class PoradcaScopeUpdate(BaseModel):
    """Change of „what we are talking about". ``version_id = None`` = the whole project (the field is required so that
    omission would not mean an unwanted change)."""

    version_id: Optional[UUID]


class PoradcaRename(BaseModel):
    """New conversation name — one line; spaces and line breaks merge into a single space."""

    title: str = Field(..., min_length=1, max_length=TITLE_MAX_CHARS)

    @field_validator("title", mode="before")
    @classmethod
    def _one_line(cls, value: object) -> object:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("title")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        return _refuse_nul(value)


class PoradcaVersionInfo(BaseModel):
    """Project version for the „what we are talking about" choice — with the phase and build state."""

    id: UUID
    version_number: str
    stage: Optional[str] = None
    status: Optional[str] = None
    #: Can an instruction from Poradca be inserted into the Control Centre field of this version now?
    instruction_open: bool = False
    #: Why not — a sentence for a button greyed out with a reason.
    instruction_closed_reason: Optional[str] = None


class PoradcaProjectContext(BaseModel):
    project_id: UUID
    project_slug: str
    project_name: str
    versions: list[PoradcaVersionInfo] = Field(default_factory=list)


class PoradcaStatus(BaseModel):
    """Whether Poradca can run — for the screen (greyed out with a reason) and for ``/health``."""

    ready: bool
    problems: list[str] = Field(default_factory=list)
    running: int
    max_concurrent: int
    #: DEV-52 — stropy priložených snímok, aby obrazovka odmietla priveľký obrázok hneď pri vložení.
    attachment_max_bytes: int = 0
    attachments_max_count: int = 0
    attachments_max_total_bytes: int = 0
    attachment_types: list[str] = Field(default_factory=list)
    #: DEV-103 — how long a question may be, so a question composed from a decision card fits before it is sent.
    question_max_chars: int = MAX_QUESTION_CHARS


# --- DEV-119: a ticket in Plane from a Poradca answer ------------------------------------------------------------

TicketKind = Literal["nsv", "project"]
TicketWay = Literal["version", "fast_fix"]


class PoradcaTicketProposalRead(BaseModel):
    kind: TicketKind = Field(description="nsv = a problem of the cockpit for Andros Dedo; project = project work.")
    title: str


class PoradcaSimilarTicketRead(BaseModel):
    identifier: str
    title: str
    state: str
    url: str


class PoradcaTicketDraft(BaseModel):
    """What „Založiť tiket" offers under a Poradca answer — or the ticket founded from it."""

    kind: TicketKind
    title: str
    description: str
    ticket_type: TicketType
    work_kind: Optional[WorkKind] = None
    way: Optional[TicketWay] = Field(default=None, description="Poradca's proposal: the next version or a fast fix.")
    responsible_options: list[str]
    responsible: Optional[str] = None
    context: list[str] = Field(default_factory=list, description="What the cockpit adds to a ticket of the cockpit.")
    similar: list[PoradcaSimilarTicketRead] = Field(default_factory=list)
    plane_identifier: Optional[str] = None
    plane_url: Optional[str] = None


class PoradcaTicketCreate(BaseModel):
    """The edited draft. A project ticket needs its work kind, the proposed way and who is responsible."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    ticket_type: TicketType
    work_kind: Optional[WorkKind] = None
    way: Optional[TicketWay] = None
    responsible: Optional[str] = Field(default=None, max_length=100)


class PoradcaTicketFounded(BaseModel):
    plane_identifier: str
    plane_url: str
    created: bool


# ── DEV-318 Nápady ────────────────────────────────────────────────────────────────────────────────────────────────────


class IdeaCreate(BaseModel):
    """A new idea — its first message to Poradca: the rough idea, as text (also pasted from Obsidian) and images."""

    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    attachments: list[PoradcaAttachmentUpload] = Field(default_factory=list)
    #: DEV-350 — the name the screen means (a follow-up idea: what the person wants, not the cockpit's own first
    #: sentence); without it the idea is named by the first words of its question.
    title: Optional[str] = Field(default=None, max_length=200)

    @field_validator("question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        return _refuse_nul(value)


class IdeaRead(BaseModel):
    id: UUID
    title: str
    #: ``open`` (Rozpracovaný), ``brief_ready`` (Zadanie hotové), ``founded`` (Založený ako …),
    #: ``added`` (Pridaný do …).
    state: str
    conversation_id: UUID
    author_name: str
    #: An answer is running now.
    running: bool = False
    #: What its answers cost in euros, by the price list of Náklady; ``None`` before any priced answer.
    cost: Optional[float] = None
    outcome_project_slug: Optional[str] = None
    outcome_project_name: Optional[str] = None
    #: When the brief was added to an existing project: the Zásobník requirement's number (REQ-N).
    outcome_backlog_number: Optional[int] = None
    #: DEV-350 — the talk went on in the project's Poradca; ``False`` while open, or when it stayed with its author
    #: because he may not open the project it was added to.
    talk_in_project: bool = False
    #: The founded project's ticket in the DEV register.
    plane_identifier: Optional[str] = None
    plane_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class IdeaDetail(IdeaRead):
    conversation: PoradcaConversationDetail


class IdeaFoundedCreate(BaseModel):
    """The person founded a project from the brief of this answer (the form Nový projekt, prefilled from it)."""

    message_id: UUID
    project_id: UUID


class IdeaAddedCreate(BaseModel):
    """The brief of this answer goes to the Zásobník of the project it names."""

    message_id: UUID


class IdeaOutcome(IdeaRead):
    #: The founded project's ticket could not be filed now — the sentence why; the idea stays founded, a second
    #: click files it.
    ticket_error: Optional[str] = None
