"""The Zásobník's table goes (DEV-455).

**What happened.** v4.164.0 removed the Zásobník: a version's work is the Plane tickets put into it
(``version_tickets``, migration 154). Its requirements without a ticket were converted into project tickets on
21.11.2026 after the Director reviewed the list (DEV-461 … DEV-482; the one of NEX Finance, which is being rebuilt, on
purpose not).

**What it changes.** An idea added to a project keeps the ticket its requirement had (``ideas.plane_*``); then the
links to the Zásobník (``ideas.outcome_backlog_item_id``, ``user_notes.backlog_item_id``,
``poradca_messages.captured_backlog_item_id``) and the table ``backlog_items`` are dropped.

**Downgrade** recreates the empty table and the links — the requirements themselves are gone (their tickets stay in
Plane).

Revision ID: 155
Revises: 154
Create Date: 2026-11-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "155"
down_revision: Union[str, None] = "154"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LINKS = (
    ("ideas", "outcome_backlog_item_id"),
    ("user_notes", "backlog_item_id"),
    ("poradca_messages", "captured_backlog_item_id"),
)


def upgrade() -> None:
    op.execute(
        """
        UPDATE ideas i SET plane_issue_id = b.plane_issue_id, plane_identifier = b.plane_identifier,
                           plane_url = b.plane_url
          FROM backlog_items b
         WHERE b.id = i.outcome_backlog_item_id AND i.plane_issue_id IS NULL AND b.plane_issue_id IS NOT NULL
        """
    )
    for table, column in LINKS:
        op.drop_column(table, column)
    op.drop_table("backlog_items")


def downgrade() -> None:
    op.create_table(
        "backlog_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("realized_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delivered_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("plane_issue_id", sa.String(64), nullable=True),
        sa.Column("plane_identifier", sa.String(32), nullable=True),
        sa.Column("plane_url", sa.String(500), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    for table, column in LINKS:
        op.add_column(
            table,
            sa.Column(
                column,
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("backlog_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
