"""A Web project's second frontend has its own port, and a change of a project's type is kept (DEV-160).

Project type „web“ (backend + administration + public web) was only a plan in ``.nex-archetype.json`` that nothing
read. Director 20.10.2026: „Ideme kokpit naučiť stavať projekt s dvoma frontendmi.“ The public web (``web/``) gets the
port ``web_port`` — set exactly when the type is ``web``; no project is of that type today, so every row already
satisfies the new rule. Switching a project's type is recorded in ``project_type_changes``.

Revision ID: 131
Revises: 130
Create Date: 2026-10-20

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "131"
down_revision: Union[str, None] = "130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PORTS_DISTINCT_OLD = """
        (backend_port IS NULL OR frontend_port IS NULL OR backend_port <> frontend_port)
    AND (backend_port IS NULL OR db_port IS NULL OR backend_port <> db_port)
    AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port)
"""
_PORTS_DISTINCT_NEW = (
    _PORTS_DISTINCT_OLD
    + """
    AND (web_port IS NULL OR backend_port IS NULL OR web_port <> backend_port)
    AND (web_port IS NULL OR frontend_port IS NULL OR web_port <> frontend_port)
    AND (web_port IS NULL OR db_port IS NULL OR web_port <> db_port)
"""
)


def upgrade() -> None:
    op.add_column("projects", sa.Column("web_port", sa.Integer(), nullable=True))
    op.drop_constraint("ck_projects_ports_distinct", "projects", type_="check")
    op.create_check_constraint("ck_projects_ports_distinct", "projects", _PORTS_DISTINCT_NEW)
    op.create_check_constraint("ck_projects_web_port", "projects", "(type = 'web') = (web_port IS NOT NULL)")
    op.create_table(
        "project_type_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_type", sa.String(20), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("web_port", sa.Integer(), nullable=True),
        sa.Column(
            "changed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        # clock_timestamp(): the moment of the change, not the start of its transaction.
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
    )
    op.create_index("ix_project_type_changes_project_id", "project_type_changes", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_type_changes_project_id", table_name="project_type_changes")
    op.drop_table("project_type_changes")
    op.drop_constraint("ck_projects_web_port", "projects", type_="check")
    op.drop_constraint("ck_projects_ports_distinct", "projects", type_="check")
    op.create_check_constraint("ck_projects_ports_distinct", "projects", _PORTS_DISTINCT_OLD)
    op.drop_column("projects", "web_port")
