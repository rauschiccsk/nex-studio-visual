"""Collapse agent_terminal_sessions.role CHECK to the two v2 agents (CR-V2-007).

``ck_ats_role`` is a THIRD surviving v1 5-role CHECK (after ``OrchestratorSession.role``
and ``ck_user_agent_settings_role``, both rewritten in migration 069) — it still listed
``{designer, implementer, auditor, coordinator}`` (migrations 043 + 049), so CR-V2-007's
collapse to the two v2 agents would be DB-rejected on the next ``spawn`` / debug-attach.

This rewrites it to ``{ai-agent, auditor}``. NOTE the spelling: ``agent_terminal_sessions.role``
stores the **charter-path slug** (hyphen — it is the ``.claude/agents/<role>/CLAUDE.md`` path
component used by ``spawn``), so the allowed values are ``ai-agent`` (NOT the ``ai_agent`` DB enum
used by ``OrchestratorSession.role`` / ``current_actor``) and ``auditor``. The DB-value ↔ path-slug
bridge lives in ``orchestrator._charter_slug_for_role`` / ``db_role_for_charter_slug``.

Mirrors ``backend/db/models/agent_terminal.py`` (the ``ck_ats_role`` CheckConstraint) so the model
and the DB stay in lock-step.

Data migration (no-op on a fresh/CI DB): ``agent_terminal_sessions`` rows are ephemeral PTY-session
audit rows; any legacy v1-role rows on the dev-branch DB are DELETED before the CHECK swap (OQ-6:
the v2 branch DB starts fresh; main is frozen at v1.0.0, so live v1 data is untouched until cutover).

Revision ID: 073
Revises: 072
Create Date: 2026-06-26

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove any legacy v1-role rows so the new CHECK can be applied (OQ-6 fresh-branch strategy).
    op.execute("DELETE FROM agent_terminal_sessions WHERE role NOT IN ('ai-agent', 'auditor')")
    op.drop_constraint("ck_ats_role", "agent_terminal_sessions", type_="check")
    op.create_check_constraint(
        "ck_ats_role",
        "agent_terminal_sessions",
        "role IN ('ai-agent', 'auditor')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ats_role", "agent_terminal_sessions", type_="check")
    op.create_check_constraint(
        "ck_ats_role",
        "agent_terminal_sessions",
        "role IN ('designer', 'implementer', 'auditor', 'coordinator')",
    )
