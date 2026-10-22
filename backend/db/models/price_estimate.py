"""Orientačná cena verzie — what the cockpit measured on finished versions and what it estimates for one (DEV-154).

Director 20.10.2026: „Tento odhad by som chcel spraviť … Jedná sa skutočne len o odhad, aby zákazník vedel sa
rozhodnúť, či objedná daný projekt.“ Measured 22.10.2026 on 15 finished versions: the size of the Špecifikácia
predicts the delivered tokens poorly (each version estimated from the others: 39 % off on average, 126 % at worst),
the number of tasks in the plan well (22 %, 90 %) — so the estimate comes after Návrh, from the plan. Director
22.10.2026: „Súhlasím“.

* :class:`PriceSample` — one finished version as a measurement: its tasks and the tokens it delivered. Kept, so an
  estimate does not count every project's history again.
* :class:`PriceEstimate` — the estimate given for a version: the tokens, the rates, the amount, the spread and how
  many versions it rests on, and when. It is no invoice; the statement after the delivery says what is billed.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Boolean, Column, ForeignKey, Integer, Numeric, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class PriceSample(Base):
    __tablename__ = "price_samples"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True)
    #: The first version of a new project carries the whole skeleton of the application — measured apart.
    first_version = Column(Boolean, nullable=False)
    tasks = Column(Integer, nullable=False)
    code_tokens = Column(Integer, nullable=False)
    docs_tokens = Column(Integer, nullable=False)
    measured_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())


class PriceEstimate(Base, UUIDMixin):
    __tablename__ = "price_estimates"

    version_id = Column(
        UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    tasks = Column(Integer, nullable=False)
    first_version = Column(Boolean, nullable=False)
    code_tokens = Column(Integer, nullable=False)
    docs_tokens = Column(Integer, nullable=False)
    rate_code = Column(Numeric(12, 4), nullable=False)
    rate_docs = Column(Numeric(12, 4), nullable=False)
    amount_eur = Column(Numeric(12, 2), nullable=False)
    #: How far the measured versions strayed from their mean, in percent — ``None`` below two of them.
    spread_pct = Column(Integer, nullable=True)
    samples = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())
