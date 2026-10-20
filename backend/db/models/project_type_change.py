"""Every change of a project's type (DEV-160) — who switched it, when, from what to what.

A type is a preset of the project's frontends: switching a project from Štandardný to Web gives it a second frontend
with its own port, address and login. A change of what the project is made of is kept, not overwritten.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class ProjectTypeChange(Base, UUIDMixin):
    __tablename__ = "project_type_changes"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_type = Column(String(20), nullable=False)
    type = Column(String(20), nullable=False)
    #: The port the switch gave the web — ``None`` when the change gave no new port.
    web_port = Column(Integer, nullable=True)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # The moment of the change — ``now()`` would be the start of its transaction, the same for every change in it.
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())
