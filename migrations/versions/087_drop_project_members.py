"""Drop ``project_members`` — membership is not a concept any more.

A project belongs to the ONE user who created it (``projects.created_by``); that user may do everything
on it, nobody else sees it, and the single ``admin`` account sees everything. Two people share one login,
so there is no second account to enrol — which is what this table existed to do.

Its only functional reader was ``backend/utils/kb_access._add_assigned_projects``, which widened a
``shu`` user's Knowledge-Base access to ``projects/<slug>/`` for each project they were enrolled in. That
call site was removed in the SAME change as this migration, deliberately: it sat inside a bare
``except Exception: logger.warning(...)``, so dropping the table while leaving the call would have raised
nothing at all — a junior would simply have stopped seeing project documents, with a single warning line
as the only trace.

The table is empty (0 rows on this host at the time of writing) and was never reachable from the cockpit:
no screen ever called the endpoints. ``downgrade`` rebuilds the table exactly, so this is reversible;
what it cannot restore is rows, and there are none to lose.

Revision ID: 087
Revises: 086
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "087"
down_revision: Union[str, None] = "086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")


def downgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
