"""Dialogue between Customer and Designer agents — Gate E orchestration.

Director directive 2026-05-15: 4th ICC agent (Customer) systematically
questions Designer about application functionality to surface unfinished
spec fragments before Implementer spawn. Communication is **mediated**
through these tables — every message awaits Director approval before
delivery to the other agent, plný-gate mode.

Two tables:

* :class:`DialogueSession` — one row per Gate E session (scoped to a
  project + version). Holds session-level state (status, message
  counters, lifecycle timestamps).
* :class:`DialogueMessage` — one row per message in the conversation,
  authored by either Customer, Designer, or Director (the mediator).
  Status moves ``pending → approved → delivered``; rejected messages
  stay around as audit trail.

Schema invariants:

* ``dialogue_messages.session_id`` ON DELETE CASCADE — deleting a
  session purges its whole conversation
* ``dialogue_sessions.version_id`` ON DELETE SET NULL — versions are
  immutable post-release, but the session row should survive for audit
  trail even if a version gets archived
* CHECK constraints enforce author/status enums at DB level
"""

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class DialogueSession(Base, UUIDMixin, TimestampMixin):
    """Customer ↔ Designer dialogue session (Gate E).

    One per project + version combination; Director can have multiple
    historical sessions for the same project (each version gets fresh
    Gate E review).
    """

    __tablename__ = "dialogue_sessions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_slug = Column(String(100), nullable=False, index=True)
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(String(20), nullable=False, server_default="active")
    ended_at = Column(TIMESTAMP(timezone=True), nullable=True)
    terminated_by = Column(String(20), nullable=True)
    message_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'ended')",
            name="ck_dialogue_sessions_status",
        ),
        CheckConstraint(
            "terminated_by IS NULL OR terminated_by IN ('user', 'timeout', 'server_restart', 'coverage_complete')",
            name="ck_dialogue_sessions_terminated_by",
        ),
    )


class DialogueMessage(Base, UUIDMixin, TimestampMixin):
    """Single message in a Gate E dialogue.

    Authored by Customer (question), Designer (answer), or Director
    (intervention / injected question). Status flow:

        pending  → approved  → delivered     # happy path
        pending  → rejected                  # Director rejects, Customer regenerates

    Only ``delivered`` messages are visible to the recipient agent;
    ``pending`` await Director approval; ``rejected`` are audit trail.
    """

    __tablename__ = "dialogue_messages"

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("dialogue_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    author = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="pending")

    __table_args__ = (
        CheckConstraint(
            "author IN ('customer', 'designer', 'director')",
            name="ck_dialogue_messages_author",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'delivered', 'rejected')",
            name="ck_dialogue_messages_status",
        ),
        Index("ix_dialogue_messages_session_id", "session_id"),
        Index(
            "ix_dialogue_messages_session_created",
            "session_id",
            "created_at",
        ),
    )
