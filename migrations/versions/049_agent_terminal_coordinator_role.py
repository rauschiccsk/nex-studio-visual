"""Allow the 'coordinator' role on agent_terminal_sessions (CR-NS-009).

The 4th AgentTerminal role (Coordinator) needs to pass the
``ck_ats_role`` CHECK constraint on ``agent_terminal_sessions.role``.
Widens the allowed set from {designer, implementer, auditor} to also
include ``coordinator``.

Revision ID: 049
Revises: 048
Create Date: 2026-06-02

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_ats_role", "agent_terminal_sessions", type_="check")
    op.create_check_constraint(
        "ck_ats_role",
        "agent_terminal_sessions",
        "role IN ('designer', 'implementer', 'auditor', 'coordinator')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ats_role", "agent_terminal_sessions", type_="check")
    op.create_check_constraint(
        "ck_ats_role",
        "agent_terminal_sessions",
        "role IN ('designer', 'implementer', 'auditor')",
    )
