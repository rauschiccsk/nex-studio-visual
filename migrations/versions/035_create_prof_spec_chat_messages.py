"""Create professional_spec_chat_messages table.

Zoltán's UX feedback: chat history on the Vývojová dokumentácia
page lived only in React state and vanished on every navigation
away from the page. The backend ``/chat`` endpoint was designed
stateless with history supplied by the caller — fine for the
single-session streaming contract but a UX cliff once the user
went to Specification and back.

This migration introduces a per-spec chat message log. Schema
mirrors :mod:`architect_messages` (migration 011) — same
``role`` / ``content`` shape, ``CASCADE`` on parent delete,
timestamp-sorted list — so the service + router patterns stay
uniform across ICC AI surfaces.

Token / cost columns are omitted here; the Vývojová dokumentácia
chat is billed under the project's Claude MAX quota and the
individual turn accounting is not surfaced anywhere in the UI.
Can be added later without a schema migration (nullable columns
always welcome).

Revision ID: 035
Revises: 034
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "professional_spec_chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("professional_spec_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["professional_spec_id"],
            ["professional_specifications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_professional_spec_chat_messages_role",
        ),
    )
    op.create_index(
        "ix_professional_spec_chat_messages_spec_id",
        "professional_spec_chat_messages",
        ["professional_spec_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_professional_spec_chat_messages_spec_id",
        table_name="professional_spec_chat_messages",
    )
    op.drop_table("professional_spec_chat_messages")
