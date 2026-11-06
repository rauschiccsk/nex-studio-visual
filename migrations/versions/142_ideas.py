"""Nápady — a new idea talked over with Poradca before any project exists (DEV-318).

**What happened.** A new project went from a rough idea to a brief for the cockpit only through Andros Dedo; Poradca
worked only inside a pinned project (``poradca_conversations.project_id`` NOT NULL). Director 06.11.2026: „Návrhy
nových projektov sa mi zhromažďujú. Navrhni mi riešenie, aby som mohol tie návrhy robiť pokiaľ ty pracuješ nad
kokpitom.“ — „Robiť nový projekt pod iným cudzím projektom, je nelogické.“

**What it adds.**
* ``ideas`` — one person's new idea and where it ended (a project founded from it, or a requirement added to one);
* ``poradca_conversations.idea_id`` — the conversation that is the idea's talk; ``project_id`` may now be empty
  while it is an idea, and a check keeps every conversation about a project or an idea.

**Downgrade** deletes the conversations of ideas that never got a project (they have nothing to belong to), then
the column, the check and the table.

Revision ID: 142
Revises: 141
Create Date: 2026-11-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "142"
down_revision: Union[str, None] = "141"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ideas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column(
            "outcome_project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "outcome_backlog_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("backlog_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "outcome_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("poradca_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plane_issue_id", sa.String(64), nullable=True),
        sa.Column("plane_identifier", sa.String(32), nullable=True),
        sa.Column("plane_url", sa.String(500), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('founded', 'added')", name="ck_ideas_outcome"),
    )
    op.add_column(
        "poradca_conversations",
        sa.Column("idea_id", UUID(as_uuid=True), sa.ForeignKey("ideas.id", ondelete="RESTRICT"), nullable=True),
    )
    op.create_unique_constraint("uq_poradca_conversations_idea_id", "poradca_conversations", ["idea_id"])
    op.alter_column("poradca_conversations", "project_id", nullable=True)
    op.create_check_constraint(
        "ck_poradca_conversations_subject", "poradca_conversations", "project_id IS NOT NULL OR idea_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM poradca_messages WHERE conversation_id IN "
        "(SELECT id FROM poradca_conversations WHERE project_id IS NULL)"
    )
    op.execute("DELETE FROM poradca_conversations WHERE project_id IS NULL")
    op.drop_constraint("ck_poradca_conversations_subject", "poradca_conversations", type_="check")
    op.alter_column("poradca_conversations", "project_id", nullable=False)
    op.drop_constraint("uq_poradca_conversations_idea_id", "poradca_conversations", type_="unique")
    op.drop_column("poradca_conversations", "idea_id")
    op.drop_table("ideas")
