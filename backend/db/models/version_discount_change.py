"""Every change of a version's own discounts per billing category (DEV-75) — who, when, from what to what.

The discounts (``versions.discount_*``) override the defaults in Nastavenia for the version's delivery statement;
empty = the default. The statement is the basis of an invoice, so a change of what it is counted with is kept.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class VersionDiscountChange(Base, UUIDMixin):
    __tablename__ = "version_discount_changes"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    #: ``None`` = the default from Nastavenia applied (before) / applies (after).
    previous_discount_bug = Column(Numeric(5, 2), nullable=True)
    previous_discount_request = Column(Numeric(5, 2), nullable=True)
    previous_discount_gift = Column(Numeric(5, 2), nullable=True)
    discount_bug = Column(Numeric(5, 2), nullable=True)
    discount_request = Column(Numeric(5, 2), nullable=True)
    discount_gift = Column(Numeric(5, 2), nullable=True)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: clock_timestamp(): the moment of the change, not the start of its transaction.
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())
