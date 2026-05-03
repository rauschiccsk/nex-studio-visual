"""Create ui_design_chat_messages table.

Mirror of migration 035 (``professional_spec_chat_messages``) for
the UIDesign chat panel. Zoltán wants the same navigation-survival
behaviour on Krok 2B as on Krok 2A.

Revision ID: 036
Revises: 035
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ui_design_chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ui_design_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ui_design_id"], ["ui_designs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_ui_design_chat_messages_role",
        ),
    )
    op.create_index(
        "ix_ui_design_chat_messages_ui_design_id",
        "ui_design_chat_messages",
        ["ui_design_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ui_design_chat_messages_ui_design_id",
        table_name="ui_design_chat_messages",
    )
    op.drop_table("ui_design_chat_messages")
