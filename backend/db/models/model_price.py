"""Anthropic model price list by which the cockpit prices agent spend (ICCINT-168).

Prices are not entered by hand: the cockpit reads them off turns paid for by Claude Code (the difference of its price
totals in the session log) and checks that they reproduce Claude Code prices — :mod:`backend.services.model_pricing`.
Director 06.10.2026: „Ak tie ceny nepoužívame už prosím ich upratať, je zmetkujúce ak niečo tam je uvedené
a v skutočnosti sa používa niečo iné."

One row = the price list of one model from ``valid_from``. When Anthropic changes a price, a new row is added —
the old one stays, as older turns were computed with it. The euro rate is bound to the list: it is downloaded once, when
it is created, not on every display (Director: „kurz zistiť z verejne dostupného zdroja len pri zmene ceny").
"""

from sqlalchemy import TIMESTAMP, Column, Date, Float, Integer, String, UniqueConstraint

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class ModelPrice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "model_prices"

    #: Full model name as Claude Code reports it (``claude-<family>-<version>``) — the list is bound to the version.
    model = Column(String(100), nullable=False)
    #: The first turn this price list covers.
    valid_from = Column(TIMESTAMP(timezone=True), nullable=False)
    #: Prices in dollars per million tokens.
    input_usd = Column(Float, nullable=False)
    output_usd = Column(Float, nullable=False)
    cache_read_usd = Column(Float, nullable=False)
    cache_write_usd = Column(Float, nullable=False)
    #: Price of one web search — Claude Code bills it separately. Empty until the model had any.
    web_search_usd = Column(Float, nullable=True)
    #: How many dollars one euro costs (ECB reference rate, as the bank publishes it). Empty until the
    #: rate could be downloaded — the euro price is not computed until then.
    eur_usd = Column(Float, nullable=True)
    rate_date = Column(Date, nullable=True)
    rate_source = Column(String(200), nullable=True)
    #: On how many turns paid by Claude Code the list is verified, and the largest relative deviation from its prices.
    observations = Column(Integer, nullable=False)
    max_deviation = Column(Float, nullable=False)
    #: The newest paid turn already compared with the price list (newer ones are compared at the next check).
    checked_until = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("model", "valid_from", name="uq_model_prices_model_valid_from"),)
