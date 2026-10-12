"""What the manager tried in UAT — one row per customer, version and change of the test guide (DEV-91)."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

#: The change works as the guide expects.
TESTED = "tested"
#: The change does not work — the note says what happened.
BROKEN = "broken"


class UatCheck(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "uat_checks"

    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    #: The change as the guide titles it (or the release note's text for a version without a guide).
    item = Column(Text, nullable=False)
    status = Column(String(10), nullable=False)
    note = Column(Text, nullable=True)
    checked_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('tested', 'broken')", name="ck_uat_checks_status"),
        UniqueConstraint("customer_id", "version_id", "item", name="uq_uat_checks_item"),
    )
