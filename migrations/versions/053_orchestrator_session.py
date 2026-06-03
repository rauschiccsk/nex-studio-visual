"""Create orchestrator_session (F-007 Orchestration Cockpit, CR-NS-018 Phase 2).

One headless ``claude`` session UUID per ``(project_slug, role)`` — the
orchestrator resolves/creates it here and ``--resume``s it. Keyed by
(project_slug, role), never by user, so two Directors of one project share one
agent conversation.

Revision ID: 053
Revises: 052
Create Date: 2026-06-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orchestrator_session",
        sa.Column("project_slug", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("claude_session_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('coordinator', 'designer', 'customer', 'implementer', 'auditor')",
            name="ck_orchestrator_session_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_slug", "role", name="uq_orchestrator_session_project_role"),
    )


def downgrade() -> None:
    op.drop_table("orchestrator_session")
