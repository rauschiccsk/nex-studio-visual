"""Poradca — conversations with an agent that only reads and advises (ICCINT-167).

**What happened.** People who design in the cockpit had nowhere to ask what they did not understand, nor whom to
send to look at things „from behind" — logs, the build agent's steps, the UAT database. Consulting on a finished version
ran through the version state (not possible during the build), saw only code and nobody used it on the live cockpit.
Director approved Poradca on 2026-10-05: a separate cockpit item, runs alongside the build, read-only.

**What it adds.**
* ``poradca_conversations`` — one person's conversation about a project (about: a version or the whole project);
* ``poradca_messages`` — questions and answers; from the progress only steps (tool and target), never content;
* the ``poradca`` role in ``user_agent_settings`` — Poradca's model and effort are set as for the other
  agentoch.

**Downgrade** drops the tables and deletes the ``poradca`` role settings — the constraint would not let them through.

Revision ID: 102
Revises: 101
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "102"
down_revision: Union[str, None] = "101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLE_CHECK = "ck_user_agent_settings_role"
_ROLES_OLD = "('ai_agent', 'auditor')"
_ROLES_NEW = "('ai_agent', 'auditor', 'poradca')"


def upgrade() -> None:
    op.create_table(
        "poradca_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("claude_session_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_poradca_conversations_project_author", "poradca_conversations", ["project_id", "author_id"])

    op.create_table(
        "poradca_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("poradca_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("author", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("steps", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="done"),
        sa.Column("usage", JSONB, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "captured_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("author IN ('human', 'poradca')", name="ck_poradca_messages_author"),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed', 'stopped')",
            name="ck_poradca_messages_status",
        ),
    )
    op.create_index("ix_poradca_messages_conversation_seq", "poradca_messages", ["conversation_id", "seq"])

    op.drop_constraint(_ROLE_CHECK, "user_agent_settings", type_="check")
    op.create_check_constraint(_ROLE_CHECK, "user_agent_settings", f"agent_role IN {_ROLES_NEW}")


def downgrade() -> None:
    op.execute("DELETE FROM user_agent_settings WHERE agent_role = 'poradca'")
    op.drop_constraint(_ROLE_CHECK, "user_agent_settings", type_="check")
    op.create_check_constraint(_ROLE_CHECK, "user_agent_settings", f"agent_role IN {_ROLES_OLD}")
    op.drop_index("ix_poradca_messages_conversation_seq", table_name="poradca_messages")
    op.drop_table("poradca_messages")
    op.drop_index("ix_poradca_conversations_project_author", table_name="poradca_conversations")
    op.drop_table("poradca_conversations")
