"""Add claude_session_id to agent_terminal_sessions for auto-resume.

Director directive 2026-05-19: AG terminal scrollback was lost after
re-login because output buffer lived only in RAM. Fix: persist output
to disk + survive BE restart via claude CLI ``--resume <uuid>``.

This column stores the claude CLI session UUID assigned at spawn time
(``claude --session-id <uuid> --append-system-prompt <charter>``).
Subsequent respawns after BE restart use ``claude --resume <uuid>``
to continue AI conversation memory.

Nullable for legacy rows (sessions spawned before this migration —
they cannot auto-resume and will be marked terminated_by='server_restart'
as before).

Revision ID: 046
Revises: 045
Create Date: 2026-05-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_terminal_sessions",
        sa.Column("claude_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_terminal_sessions", "claude_session_id")
