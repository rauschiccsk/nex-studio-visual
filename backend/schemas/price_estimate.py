"""Orientačná cena verzie (DEV-154)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


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
    created_at: datetime
