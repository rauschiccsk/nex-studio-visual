"""Every change of a version's own token rates (DEV-74) — who changed them, when, from what to what.

A version's rates (``versions.rate_code`` / ``rate_docs``) override Nastavenia for its delivery statement; empty =
the rate from Nastavenia. The statement is the basis of an invoice, so a change of what it is counted with is kept,
not overwritten.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class VersionRateChange(Base, UUIDMixin):
    __tablename__ = "version_rate_changes"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    #: ``None`` = the rate from Nastavenia applied (before) / applies (after).
    previous_rate_code = Column(Numeric(12, 4), nullable=True)
    previous_rate_docs = Column(Numeric(12, 4), nullable=True)
    rate_code = Column(Numeric(12, 4), nullable=True)
    rate_docs = Column(Numeric(12, 4), nullable=True)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # The moment of the change — ``now()`` would be the start of its transaction, the same for every change in it.
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())
