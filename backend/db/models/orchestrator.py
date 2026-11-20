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
    TIMESTAMP,
    CheckConstraint,
    Column,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class OrchestratorSession(Base, UUIDMixin, TimestampMixin):
    """One headless claude session UUID per ``(project_slug, role)`` (F-007 §5.1)."""

    __tablename__ = "orchestrator_session"

    project_slug = Column(String(100), nullable=False)
    role = Column(String(16), nullable=False)
    claude_session_id = Column(UUID(as_uuid=True), nullable=False)
    #: R1 session hygiene (v0.7.0, D3): last time a turn was driven on this ``(project, role)`` thread,
    #: bumped on every ``invoke_agent``. Powers the conservative 7-day TTL retention task
    #: (``cleanup_old_orchestrator_sessions``) that mirrors ``agent_terminal.idle_cleanup`` — bounds
    #: unbounded row growth without expiring an actively-used thread. Defaults to ``now()`` (≈ ``created_at``
    #: at insert; the migration backfills existing rows to ``created_at``).
    last_input_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    #: DEV-448: the fingerprint (SHA-256) of the Pravidlá agenta this session last received — at its start or as the
    #: current text given to a resumed turn after the charter changed. Empty for a session from before.
    charter_sha256 = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_slug", "role", name="uq_orchestrator_session_project_role"),
        CheckConstraint(
            # v2.0.0 (CR-V2-001): two agent roles — AI Agent (doer) + Auditor (independent verifier).
            "role IN ('ai_agent', 'auditor')",
            name="ck_orchestrator_session_role",
        ),
    )
