"""Schémy Poradcu (ICCINT-167)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Najdlhšia otázka — dlhší text je skôr vložený log; ten si Poradca prečíta sám nástrojom.
MAX_QUESTION_CHARS = 20_000
#: Najdlhší názov rozhovoru — šírka stĺpca ``poradca_conversations.title``.
TITLE_MAX_CHARS = 200


def _refuse_nul(value: str) -> str:
    """Znak NUL databáza do textu neuloží (pád 500) — odmietne sa vetou, nie chybou servera."""
    if "\x00" in value:
        raise ValueError("Text nesmie obsahovať znak NUL.")
    return value


class PoradcaStep(BaseModel):
    """Čo Poradca urobil — nástroj a cieľ, nikdy obsah."""

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
    #: Úplné meno modelu tak, ako ho hlási Claude Code; ``None`` pri otázke a pri nedokončenej odpovedi.
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    #: Cena odpovede v EUR tou istou cestou ako stavba; ``None``, keď model nemá nastavenú cenu.
    cost: Optional[float] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    #: Odpoveď nesie pokyn pre agenta bežiacej stavby (blok ``<pokyn-pre-agenta>``) — text na vloženie.
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
    #: O čom sa rozprávame — ``None`` = celý projekt.
    version_id: Optional[UUID] = None
    version_number: Optional[str] = None
    author_id: UUID
    author_name: str
    title: str
    #: Odpoveď práve beží.
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
    """Zmena „o čom sa rozprávame". ``version_id = None`` = celý projekt (pole je povinné, aby
    vynechanie neznamenalo nechcenú zmenu)."""

    version_id: Optional[UUID]


class PoradcaRename(BaseModel):
    """Nový názov rozhovoru — jeden riadok; medzery a zalomenia sa zlejú do jednej medzery."""

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
    """Verzia projektu pre voľbu „o čom sa rozprávame" — s fázou a stavom stavby."""

    id: UUID
    version_number: str
    stage: Optional[str] = None
    status: Optional[str] = None
    #: Dá sa teraz pokyn od Poradcu vložiť do poľa Riadiaceho centra tejto verzie?
    instruction_open: bool = False
    #: Prečo nie — veta pre tlačidlo zašednuté s dôvodom.
    instruction_closed_reason: Optional[str] = None


class PoradcaProjectContext(BaseModel):
    project_id: UUID
    project_slug: str
    project_name: str
    versions: list[PoradcaVersionInfo] = Field(default_factory=list)


class PoradcaStatus(BaseModel):
    """Či Poradca vie bežať — pre obrazovku (zašednutie s dôvodom) aj ``/health``."""

    ready: bool
    problems: list[str] = Field(default_factory=list)
    running: int
    max_concurrent: int
    #: DEV-52 — stropy priložených snímok, aby obrazovka odmietla priveľký obrázok hneď pri vložení.
    attachment_max_bytes: int = 0
    attachments_max_count: int = 0
    attachments_max_total_bytes: int = 0
    attachment_types: list[str] = Field(default_factory=list)
