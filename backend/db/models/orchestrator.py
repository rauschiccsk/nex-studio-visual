"""Orchestrator agent session mapping — F-007 Orchestration Cockpit (CR-NS-018 Phase 2).

The orchestrator drives each agent headless via ``claude -p --resume <uuid>``.
The conversation must be the **same per (project, role)** regardless of which
Director runs the board (two Directors of one project share one agent thread),
so the claude session UUID is stored pipeline-side keyed ``(project_slug, role)``
— deliberately NOT keyed by user (that would fork the conversation per Director).

The Phase-4 debug terminal (F-007 §10) attaches by lazily creating a
Director-owned ``agent_terminal_sessions`` row that ``--resume``s this UUID.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class OrchestratorSession(Base, UUIDMixin, TimestampMixin):
    """One headless claude session UUID per ``(project_slug, role)`` (F-007 §5.1)."""

    __tablename__ = "orchestrator_session"

    project_slug = Column(String(100), nullable=False)
    role = Column(String(16), nullable=False)
    claude_session_id = Column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_slug", "role", name="uq_orchestrator_session_project_role"),
        CheckConstraint(
            "role IN ('coordinator', 'designer', 'customer', 'implementer', 'auditor')",
            name="ck_orchestrator_session_role",
        ),
    )
