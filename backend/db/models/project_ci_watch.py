"""What the cockpit last saw on a project's main branch on GitHub — so a failed run is told once (DEV-85).

12.10.2026 the Director follows CI on a second monitor; the cockpit now tells the project's addressee on Telegram when a
run on the main branch fails, and once more when the branch is green again. One row per project: the branch watched,
the newest finished run of each workflow on it, and the messages Telegram did not take yet (tried again). Kept in the
database, so a restart or a deploy of the cockpit never sends the same message twice.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, TimestampMixin


class ProjectCiWatch(Base, TimestampMixin):
    __tablename__ = "project_ci_watch"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    branch = Column(String(255), nullable=False)
    #: ``{workflow: {run_id, attempt, number, state, subject, failed_step, url}}`` — the newest finished run of each.
    runs = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    #: ``[{text, at}]`` — messages Telegram refused; tried again until ``ci_watch_retry_hours`` pass.
    undelivered = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
