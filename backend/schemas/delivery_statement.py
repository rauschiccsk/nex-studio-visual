"""DEV-50 — the delivered-token statement of a version as the screen reads it."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from backend.schemas.price_estimate import PriceEstimateRead
from backend.schemas.task import BillingCategory

#: DEV-50: a fix of an error in delivered code, or a change; DEV-118: or a gift — an attention of ICC for the whole
#: piece of work (Director 14.10.2026: „BUG - oprava, GIFT - Dar, a požiadavka zákazníka.“).
WorkKind = Literal["fix", "change", "gift"]


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


class StatementLineTaskRead(BaseModel):
    """DEV-75 — a task whose work is on a line of the statement."""

    label: str = Field(description="epic.feat.task — the number in the plan and in the agent's commits.")
    title: str
    lines: int
    tokens: int


class StatementLineRead(BaseModel):
    """DEV-75 — one line of the statement: a category × kind, its full value, the discount and the amount."""

    category: BillingCategory
    kind: Literal["code", "docs"]
    tasked: bool = Field(
        description="False = work without a task (design documents, the visual phase, fixes after verification).",
    )
    label: str
    lines: int
    tokens: int
    rate: Decimal
    full_eur: Decimal
    discount_pct: Decimal
    discount_label: Optional[str] = Field(default=None, description="„záručná oprava“, „pozornosť ICC“, „zľava“.")
    discount_eur: Decimal
    amount_eur: Decimal
    tasks: list[StatementLineTaskRead] = []


class StatementTaskRead(BaseModel):
    """DEV-75 — a task of the version's plan with its category and what it delivered."""

    id: UUID
    label: str
    title: str
    epic_title: str = ""
    own_category: Optional[BillingCategory] = Field(default=None, description="None = the version's category.")
    category: Optional[BillingCategory] = Field(default=None, description="As it counts; None while undecided.")
    lines: int
    tokens: int


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
    #: The same, as the statement says it („stav, na ktorom prešla Verifikácia“) — one wording for screen and CSV.
    delivered_source_label: Optional[str] = None
    tokenizer: str
    code: Optional[KindTotalRead] = None
    tests: Optional[KindTotalRead] = None
    docs: Optional[KindTotalRead] = None
    files: list[DeliveredFileRead] = []
    rate_code: Decimal
    rate_docs: Decimal
    # DEV-74 — the rates in Nastavenia, and whether this version counts with its own instead.
    settings_rate_code: Decimal = Decimal("0")
    settings_rate_docs: Decimal = Decimal("0")
    rate_code_own: bool = False
    rate_docs_own: bool = False
    #: € per line — code and tests, documentation — each rounded to cents; ``amount_eur`` is their sum.
    #: ``None`` while the work kind is undecided.
    amount_code_eur: Optional[Decimal] = None
    amount_docs_eur: Optional[Decimal] = None
    amount_eur: Optional[Decimal] = None
    #: Why it cannot be issued yet (kind undecided, rates missing) — empty when it can.
    cannot_issue: list[str] = []
    calibration: Optional[CalibrationRead] = None
    # DEV-75 — categories and discounts.
    version_category: Optional[BillingCategory] = Field(
        default=None, description="The category of a task nobody decided; None while the work kind is undecided."
    )
    discounts: dict[BillingCategory, Decimal] = Field(default_factory=dict, description="In %, as the version counts.")
    settings_discounts: dict[BillingCategory, Decimal] = Field(default_factory=dict)
    discounts_own: dict[BillingCategory, bool] = Field(default_factory=dict)
    lines: list[StatementLineRead] = []
    tasks: list[StatementTaskRead] = []
    full_eur: Optional[Decimal] = Field(default=None, description="What the delivery was worth before the discounts.")


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
    #: Frozen with the statement; ``None`` only on one issued before the lines were kept that would not add up.
    amount_code_eur: Optional[Decimal] = None
    amount_docs_eur: Optional[Decimal] = None
    amount_eur: Decimal
    #: DEV-54 — the statement this one replaced, and when this one was replaced in turn (``None`` = valid).
    replaces_id: Optional[UUID] = None
    replaced_at: Optional[datetime] = None
    #: DEV-75 — the lines it was issued with; ``None`` on one issued before the categories (two lines by kind).
    lines: Optional[list[StatementLineRead]] = None

    model_config = {"from_attributes": True}


class RateChangeRead(BaseModel):
    """DEV-74 — one change of a version's own rates; ``None`` = the rate in Nastavenia."""

    created_at: datetime
    changed_by: Optional[str] = Field(default=None, description="Who changed them — the person's name.")
    previous_rate_code: Optional[Decimal] = None
    previous_rate_docs: Optional[Decimal] = None
    rate_code: Optional[Decimal] = None
    rate_docs: Optional[Decimal] = None


class RatesWrite(BaseModel):
    """DEV-74 — a version's own rates per 1 000 tokens; ``null`` = back to the rate in Nastavenia."""

    rate_code: Optional[Decimal] = None
    rate_docs: Optional[Decimal] = None


class DiscountChangeRead(BaseModel):
    """DEV-75 — one change of a version's own discounts, in %; ``None`` = the discount in Nastavenia."""

    created_at: datetime
    changed_by: Optional[str] = Field(default=None, description="Who changed them — the person's name.")
    previous: dict[BillingCategory, Optional[Decimal]]
    current: dict[BillingCategory, Optional[Decimal]]


class DiscountsWrite(BaseModel):
    """DEV-75 — a version's own discount per category in %; ``null`` = back to the discount in Nastavenia."""

    bug: Optional[Decimal] = None
    request: Optional[Decimal] = None
    gift: Optional[Decimal] = None


class TaskCategoriesWrite(BaseModel):
    """DEV-75 — the category of these tasks (all of one epic or feat at once); ``null`` = the version's."""

    task_ids: list[UUID] = Field(min_length=1)
    category: Optional[BillingCategory] = None


class TaskCategoriesRead(BaseModel):
    changed: int


class DeliveryStatementView(BaseModel):
    preview: StatementPreviewRead
    issued: list[IssuedStatementRead]
    rate_changes: list[RateChangeRead] = Field(default_factory=list, description="DEV-74, the newest first.")
    discount_changes: list[DiscountChangeRead] = Field(default_factory=list, description="DEV-75, the newest first.")


class IssueStatementWrite(BaseModel):
    """DEV-54 — the valid statement the Manažér confirmed to replace; empty when the version has none yet."""

    replace: Optional[UUID] = None


class WorkKindWrite(BaseModel):
    work_kind: WorkKind


class WorkKindRead(BaseModel):
    work_kind: Optional[WorkKind] = None


class BillingRowRead(BaseModel):
    """DEV-73 — one version on the billing tab."""

    version_id: UUID
    version_number: str
    flow_type: Optional[str] = None
    state: Literal["issued", "ready", "needs_kind", "cannot", "blocked", "not_done"]
    reason: Optional[str] = None
    work_kind: Optional[WorkKind] = None
    base_sha: Optional[str] = None
    delivered_sha: Optional[str] = None
    delivered_source_label: Optional[str] = None
    tokens_code: Optional[int] = Field(default=None, description="Code and tests, as the statement bills them.")
    tokens_docs: Optional[int] = None
    preview_amount_eur: Optional[Decimal] = Field(default=None, description="What a statement issued now would say.")
    issued: Optional[IssuedStatementRead] = Field(default=None, description="The valid statement, if any.")
    differs: bool = Field(default=False, description="Issued, but counted today the amount would differ.")
    estimate: Optional[PriceEstimateRead] = Field(
        default=None,
        description="DEV-154, DEV-225: the orientačná cena from the version's plan — beside the statement's amount.",
    )
    rate_code: Optional[Decimal] = Field(default=None, description="DEV-74: the rates the version counts with now.")
    rate_docs: Optional[Decimal] = None
    own_rates: bool = Field(default=False, description="DEV-74: the version has a rate of its own.")


class BillingOverviewRead(BaseModel):
    """DEV-73 — every version of a project for invoicing, the newest first, with the totals."""

    rows: list[BillingRowRead]
    issued_total_eur: Decimal
    ready_total_eur: Decimal = Field(description="Not issued yet and issuable now — preview amounts.")
    needs_kind: int = Field(description="Fast fixes whose work kind waits for the manager.")
