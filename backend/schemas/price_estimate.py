"""Orientačná cena verzie (DEV-154) — on the billing tab, beside the statement (DEV-225)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, computed_field

from backend.core.kilotokens import kilotokens


class PriceEstimateRead(BaseModel):
    """An estimate, not an invoice — the statement after the delivery says what is billed."""

    tasks: int
    #: The first version of a new project — measured apart, it carries the skeleton of the application.
    first_version: bool
    code_tokens: int
    docs_tokens: int
    rate_code: Decimal
    rate_docs: Decimal
    code_eur: Decimal
    docs_eur: Decimal
    amount_eur: Decimal
    spread_pct: Optional[int] = None
    #: How many finished versions the estimate rests on, and whether that is enough to be firm.
    samples: int
    enough: bool

    @computed_field  # DEV-263: Fakturácia shows tokens in kT
    @property
    def code_kilotokens(self) -> Decimal:
        return kilotokens(self.code_tokens)

    @computed_field
    @property
    def docs_kilotokens(self) -> Decimal:
        return kilotokens(self.docs_tokens)

    created_at: datetime
