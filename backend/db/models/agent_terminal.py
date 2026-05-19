"""Agent terminal session — tracks embedded claude CLI processes spawned
from the NEX Studio Designer / Implementer / Auditor pages.

Each row represents one PTY-backed claude CLI process running inside the
backend container on behalf of an ``ri`` user. Lifecycle:

* Created when the user picks a project in the agent-terminal UI and the
  backend successfully spawns ``claude --append-system-prompt …`` via
  :mod:`backend.services.agent_terminal`.
* ``ended_at`` + ``exit_code`` + ``terminated_by`` filled when the process
  exits (idle TTL, explicit End button, crash, or BE restart).
* While ``ended_at IS NULL`` the row represents the **active** session
  for that ``(user_id, role)`` pair — partial unique index enforces
  single-session-per-role-per-user (Director directive 2026-05-13).

Memory-resident state (PTY output ring buffer, asyncio reader task) lives
in :mod:`backend.services.agent_terminal`, **not** here. This table is
the audit trail + reattach lookup only.
"""

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class AgentTerminalSession(Base, UUIDMixin, TimestampMixin):
    """Embedded agent terminal session (one claude CLI process per row)."""

    __tablename__ = "agent_terminal_sessions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False)
    project_slug = Column(String(255), nullable=False)
    pid = Column(Integer, nullable=False)
    ended_at = Column(TIMESTAMP(timezone=True), nullable=True)
    exit_code = Column(Integer, nullable=True)
    terminated_by = Column(String(20), nullable=True)
    last_activity_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    #: claude CLI session UUID — disk-persisted by claude itself.
    #: Used for ``claude --resume <uuid>`` after BE restart so AI memory
    #: continues. Nullable for legacy rows from before migration 046.
    #: Director directive 2026-05-19: auto-resume on first attach.
    claude_session_id = Column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('designer', 'implementer', 'auditor')",
            name="ck_ats_role",
        ),
        CheckConstraint(
            "terminated_by IS NULL OR terminated_by IN ('idle', 'user', 'crash', 'server_restart')",
            name="ck_ats_terminated_by",
        ),
        Index(
            "uq_ats_user_role_active",
            "user_id",
            "role",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
    )
