"""DEV-50 — the delivered-token statement of a version as the screen reads it."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

WorkKind = Literal["fix", "change"]


class DeliveredFileRead(BaseModel):
    path: str
    #: ``kod`` · ``skusky`` · ``dokumentacia``; ``None`` for a file that is left out.
    kind: Optional[str] = None
    #: Why the file is left out — a sentence for the Manažér; ``None`` for a counted file.
    excluded: Optional[str] = None
    lines: int
    tokens: int


class KindTotalRead(BaseModel):
    tokens: int
    lines: int


class CalibrationRead(BaseModel):
    """What the agent's work on the version cost (Náklady), per kind and per 1 000 delivered tokens."""

    eur_code: Optional[float] = None
    eur_docs: Optional[float] = None
    per_1k_code: Optional[float] = None
    per_1k_docs: Optional[float] = None
    complete: bool = True


class StatementPreviewRead(BaseModel):
    version_number: str
    #: The statement cannot be counted at all (version not done, delivered state unknown…) — the sentence why.
    blocked: Optional[str] = None
    #: ``None`` = undecided (a fast fix nobody classified) — it cannot be issued until it is decided.
    work_kind: Optional[WorkKind] = None
    base_sha: Optional[str] = None
    delivered_sha: Optional[str] = None
    #: ``hotovo`` (the sign-off) · ``verifikacia`` (the commit Verifikácia passed on) · ``znacka`` (the version tag).
    delivered_source: Optional[str] = None
    tokenizer: str
    code: Optional[KindTotalRead] = None
    tests: Optional[KindTotalRead] = None
    docs: Optional[KindTotalRead] = None
    files: list[DeliveredFileRead] = []
    rate_code: Decimal
    rate_docs: Decimal
    amount_eur: Optional[Decimal] = None
    #: Why it cannot be issued yet (kind undecided, rates missing) — empty when it can.
    cannot_issue: list[str] = []
    calibration: Optional[CalibrationRead] = None


class IssuedStatementRead(BaseModel):
    id: UUID
    created_at: datetime
    work_kind: WorkKind
    base_sha: str
    delivered_sha: str
    delivered_source: str
    tokenizer: str
    tokens_code: int
    tokens_tests: int
    tokens_docs: int
    rate_code: Decimal
    rate_docs: Decimal
    amount_eur: Decimal

    model_config = {"from_attributes": True}


class DeliveryStatementView(BaseModel):
    preview: StatementPreviewRead
    issued: list[IssuedStatementRead]


class WorkKindWrite(BaseModel):
    work_kind: WorkKind


class WorkKindRead(BaseModel):
    work_kind: Optional[WorkKind] = None
