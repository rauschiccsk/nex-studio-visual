"""DEV-483 — the summary of the fast path with Poradca and the switch per account."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BatchReviewRow(BaseModel):
    """One card: the Staviteľ's recommendation beside Poradca's word."""

    key: str
    number: int
    question: str
    builder_label: Optional[str] = None
    builder_option_id: Optional[str] = None
    poradca_label: Optional[str] = None
    poradca_why: Optional[str] = None
    state: Literal["agree", "disagree", "manager", "missing", "waiting", "decided"]
    #: The option both chose — sent by „Schváliť označené“; only on an agreement.
    option_id: Optional[str] = None
    decided_label: Optional[str] = None
    #: The cards whose premises this card's choice changes (DEV-34) — by number.
    related: list[int] = Field(default_factory=list)


class BatchReviewRead(BaseModel):
    status: Literal["running", "done", "failed"]
    #: Why the cards wait one by one — Poradca did not get them or did not answer.
    failure: Optional[str] = None
    conversation_id: Optional[UUID] = None
    counts: dict[str, int]
    rows: list[BatchReviewRow]


class BatchReviewAccount(BaseModel):
    id: UUID
    username: str
    name: Optional[str] = None
    on: bool


class BatchReviewSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool
