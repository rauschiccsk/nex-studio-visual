"""The Plane tickets a version solves (DEV-455).

**What happened.** The cockpit had two lists of the same work for a new version: the Zásobník (requirements REQ-N)
and the Plane tickets of the project's module. A ticket reached a version only through the Zásobník; 23 open
requirements had no ticket nobody in Plane saw, and tickets nobody copied into the Zásobník were never closed after
PROD. Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety.“

**What it changes.** Adds ``version_tickets`` — the tickets a version solves, with the ticket's text when it was put
in, when Príprava carried it and when it was closed after PROD. The requirements already in a version that have a
ticket move over (their text, delivery and the version). ``backlog_items`` stays until its requirements without a
ticket are converted (the Director reviews the list first); a later migration drops it.

**Downgrade** drops the table.

Revision ID: 154
Revises: 153
Create Date: 2026-11-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "154"
down_revision: Union[str, None] = "153"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "version_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plane_issue_id", sa.String(64), nullable=False),
        sa.Column("plane_identifier", sa.String(32), nullable=False),
        sa.Column("plane_url", sa.String(500), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("delivered_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("plane_issue_id", name="uq_version_tickets_plane_issue_id"),
    )
    op.create_index("ix_version_tickets_version_id", "version_tickets", ["version_id"])
    # A note becomes a project ticket now, not a requirement of the Zásobník.
    op.add_column(
        "user_notes",
        sa.Column(
            "ticket_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("user_notes", sa.Column("plane_identifier", sa.String(32), nullable=True))
    op.add_column("user_notes", sa.Column("plane_url", sa.String(500), nullable=True))
    op.execute(
        """
        INSERT INTO version_tickets (version_id, plane_issue_id, plane_identifier, plane_url, title, text,
                                     delivered_at, created_at, updated_at)
        SELECT DISTINCT ON (b.plane_issue_id) b.version_id, b.plane_issue_id,
               COALESCE(b.plane_identifier, b.plane_issue_id), b.plane_url, b.title, COALESCE(b.description, ''),
               b.delivered_at, b.created_at, now()
          FROM backlog_items b
         WHERE b.plane_issue_id IS NOT NULL AND b.version_id IS NOT NULL AND b.status IN ('included', 'realized')
         ORDER BY b.plane_issue_id, b.updated_at DESC
        """
    )


def downgrade() -> None:
    op.drop_column("user_notes", "plane_url")
    op.drop_column("user_notes", "plane_identifier")
    op.drop_column("user_notes", "ticket_project_id")
    op.drop_index("ix_version_tickets_version_id", table_name="version_tickets")
    op.drop_table("version_tickets")
