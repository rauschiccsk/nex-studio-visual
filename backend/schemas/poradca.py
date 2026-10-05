"""Schémy Poradcu (ICCINT-167)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Najdlhšia otázka — dlhší text je skôr vložený log; ten si Poradca prečíta sám nástrojom.
MAX_QUESTION_CHARS = 20_000


class PoradcaStep(BaseModel):
    """Čo Poradca urobil — nástroj a cieľ, nikdy obsah."""

    tool: str
    target: str = ""


class PoradcaMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author: Literal["human", "poradca"]
    content: str
    steps: list[PoradcaStep] = Field(default_factory=list)
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
    #: Odpoveď nesie požiadavku na novú verziu (blok ``<poziadavka-na-novu-verziu>``).
    new_version_request: Optional[str] = None
    #: Verzia, ktorá už z požiadavky vznikla.
    captured_version_id: Optional[UUID] = None


class PoradcaNewVersion(BaseModel):
    version_id: UUID
    version_number: str
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


class PoradcaAsk(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)


class PoradcaScopeUpdate(BaseModel):
    """Zmena „o čom sa rozprávame". ``version_id = None`` = celý projekt (pole je povinné, aby
    vynechanie neznamenalo nechcenú zmenu)."""

    version_id: Optional[UUID]


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
