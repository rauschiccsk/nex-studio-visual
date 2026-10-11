"""Poradca schemas (ICCINT-167)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class PoradcaBacklogSaved(BaseModel):
    """DEV-29: požiadavka z odpovede Poradcu uložená do Zásobníka (verzia z nej nevzniká)."""

    backlog_item_id: UUID
    number: int
    project_slug: str
    created: bool


class PoradcaConversationRead(BaseModel):
    id: UUID
    project_id: UUID
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

    @field_validator("question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        return _refuse_nul(value)


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
