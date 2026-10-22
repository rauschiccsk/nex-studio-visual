"""A deploy the cockpit refused is kept, so Poradca can read why (DEV-181).

22.10.2026 Career Asistent 1.1.0 could not be deployed to UAT — a required setting was missing — and the refusal lived
on the screen only; Poradca twice asked the Director to copy it. Director 22.10.2026: „Poradca v projekte musí vidieť
všetko.“ Kept apart from ``deploy_events``: a refusal is not a deploy.

Revision ID: 132
Revises: 131
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "132"
down_revision: Union[str, None] = "131"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deploy_refusals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.String(50), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_deploy_refusals_customer_id", "deploy_refusals", ["customer_id"])
    op.create_index("ix_deploy_refusals_project_id", "deploy_refusals", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_deploy_refusals_project_id", table_name="deploy_refusals")
    op.drop_index("ix_deploy_refusals_customer_id", table_name="deploy_refusals")
    op.drop_table("deploy_refusals")
