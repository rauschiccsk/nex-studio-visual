"""The Plane tickets a version solves (DEV-455) — the one source of a version's work.

Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety … Budeme
presne vedieť, ktoré tikety boli riešené v jednotlivých verziach.“ The Manažér puts a ticket of the project's module
into a version on the Plane tikety tab; the version keeps the ticket's text as it was then (the Príprava gets it even
when Plane does not answer), hands it to the Staviteľ and, once the version runs on PROD, moves the ticket to Done.
A ticket is solved by one version.
"""

from sqlalchemy import Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class VersionTicket(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "version_tickets"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    plane_issue_id = Column(String(64), nullable=False)
    plane_identifier = Column(String(32), nullable=False)
    plane_url = Column(String(500), nullable=True)
    title = Column(String(500), nullable=False)
    # The ticket's text without its „Robí:“ line when it was put into the version — what the Staviteľ is given.
    text = Column(Text, nullable=False, server_default="")
    added_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # When a round of Príprava carried it to the Staviteľ; NULL — the next round carries it (DEV-361).
    delivered_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # When the version ran on PROD and the ticket was moved to Done.
    closed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("plane_issue_id", name="uq_version_tickets_plane_issue_id"),)
