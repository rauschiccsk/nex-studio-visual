"""The fast path with Poradca: a switch per account and the cards Poradca assessed at once (DEV-483).

**What happened.** Director 21.11.2026: of a hundred questions of the Staviteľ he disagrees with Poradca on two or
three, yet every card went to Poradca and back by hand, one at a time. He wants the cards assessed by Poradca at once
and only a summary to approve — switchable per account (the learners keep the one-by-one way).

**What it changes.** ``users.batch_review`` (off by default) and ``batch_reviews`` — which consultation Poradca got and
its answer, or why it did not get it.

**Downgrade** drops both.

Revision ID: 156
Revises: 155
Create Date: 2026-11-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "156"
down_revision: Union[str, None] = "155"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("batch_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "batch_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consultation_seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("poradca_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "answer_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("poradca_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("version_id", "consultation_seq", name="uq_batch_reviews_version_seq"),
    )
    op.create_index("ix_batch_reviews_version_id", "batch_reviews", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_reviews_version_id", table_name="batch_reviews")
    op.drop_table("batch_reviews")
    op.drop_column("users", "batch_review")
