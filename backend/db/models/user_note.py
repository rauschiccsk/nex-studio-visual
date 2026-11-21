"""A person's quick notes in the cockpit (DEV-89) — private, kept on the server until the person sorts them out.

12.10.2026 the Director: a plain text editor for notes jotted down quickly, which stay there only until he sorts them
out properly. Only the author ever reads a note — no other user, no admin, no agent, no Poradca. ``revision`` grows
with every saved change: a change based on an older revision is refused, so a note edited on two devices is never
overwritten without a word. A resolved note stays (``resolved_at``) until deleted on purpose; a note that became a
request in a project's backlog points to it.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class UserNote(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_notes"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = Column(Text, nullable=False, server_default="")
    revision = Column(Integer, nullable=False, server_default="1")
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # DEV-455: the project ticket the note became (the Zásobník is gone) — its project, number and address in Plane.
    ticket_project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    plane_identifier = Column(String(32), nullable=True)
    plane_url = Column(String(500), nullable=True)
