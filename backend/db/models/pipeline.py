"""Orchestration pipeline models — F-007 Orchestration Cockpit (CR-NS-018).

Backend-owned single source of truth for the multi-agent pipeline. Two
tables:

* :class:`PipelineState` — one row per version (``version_id`` UNIQUE). A
  single ``SELECT`` answers "who is on turn and what's next" — the root
  problem F-007 solves. Created lazily by the orchestrator when a pipeline
  actually starts via the cockpit (never eager-seeded).
* :class:`PipelineMessage` — append-only typed message log (the
  ``.dedo-channel`` replacement). Director decisions land here as typed
  messages, giving a queryable audit trail.

Enums follow the codebase convention (``String`` + DB ``CHECK`` constraint,
not native PG ENUM). Phase 1 of F-007 §12.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

# Shared stage set (F-007 §3.1). String + CHECK rather than native enum.
_STAGES = "'kickoff', 'gate_a', 'gate_b', 'gate_c', 'gate_d', 'gate_e', 'build', 'gate_g', 'release', 'done'"
# Actors / message participants (F-007 §3.1, §4.2). ``system`` is message-only.
_ACTORS = "'coordinator', 'designer', 'customer', 'implementer', 'auditor', 'director'"
_PARTICIPANTS = "'coordinator', 'designer', 'customer', 'implementer', 'auditor', 'director', 'system'"


class PipelineState(Base, UUIDMixin, TimestampMixin):
    """Current orchestration state for one version's pipeline (F-007 §4.1)."""

    __tablename__ = "pipeline_state"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    flow_type = Column(String(16), nullable=False)
    current_stage = Column(String(16), nullable=False)
    current_actor = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    #: Human-readable "what happens next" sentence rendered on the board.
    next_action = Column(Text, nullable=False, server_default="")
    is_regate = Column(Boolean, nullable=False, server_default="false")
    iteration = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("version_id", name="uq_pipeline_state_version_id"),
        CheckConstraint(
            "flow_type IN ('new_version', 'cr', 'bug')",
            name="ck_pipeline_state_flow_type",
        ),
        CheckConstraint(
            f"current_stage IN ({_STAGES})",
            name="ck_pipeline_state_current_stage",
        ),
        CheckConstraint(
            f"current_actor IN ({_ACTORS})",
            name="ck_pipeline_state_current_actor",
        ),
        CheckConstraint(
            "status IN ('agent_working', 'awaiting_director', 'blocked', 'done')",
            name="ck_pipeline_state_status",
        ),
    )


class PipelineMessage(Base, UUIDMixin):
    """Append-only typed message in a version's pipeline (F-007 §4.2).

    Append-only — no ``updated_at`` (carries ``created_at`` only).
    """

    __tablename__ = "pipeline_message"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(16), nullable=False)
    author = Column(String(16), nullable=False)
    recipient = Column(String(16), nullable=False)
    kind = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, server_default="pending")
    payload = Column(JSONB, nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            f"stage IN ({_STAGES})",
            name="ck_pipeline_message_stage",
        ),
        CheckConstraint(
            f"author IN ({_PARTICIPANTS})",
            name="ck_pipeline_message_author",
        ),
        CheckConstraint(
            f"recipient IN ({_PARTICIPANTS})",
            name="ck_pipeline_message_recipient",
        ),
        CheckConstraint(
            "kind IN ('kickoff', 'question', 'answer', 'gate_report', 'directive', "
            "'approval', 'return', 'verdict', 'notification')",
            name="ck_pipeline_message_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'delivered', 'answered', 'archived')",
            name="ck_pipeline_message_status",
        ),
        Index("ix_pipeline_message_version_created", "version_id", "created_at"),
    )
