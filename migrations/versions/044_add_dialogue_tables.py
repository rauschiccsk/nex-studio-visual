"""Add dialogue_sessions + dialogue_messages tables.

Customer ↔ Designer dialogue orchestration (Gate E pre-Implementer
review). Director directive 2026-05-15: 4th ICC agent (Customer) asks
Designer systematic walk-through questions; communication is mediated
through these tables (plný-gate mode — Director approves each message
before delivery).

Revision ID: 044
Revises: 043
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dialogue_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_slug", sa.String(length=100), nullable=False),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("terminated_by", sa.String(length=20), nullable=True),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'ended')",
            name="ck_dialogue_sessions_status",
        ),
        sa.CheckConstraint(
            "terminated_by IS NULL OR terminated_by IN ('user', 'timeout', 'server_restart', 'coverage_complete')",
            name="ck_dialogue_sessions_terminated_by",
        ),
    )
    op.create_index(
        "ix_dialogue_sessions_user_id",
        "dialogue_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_dialogue_sessions_project_slug",
        "dialogue_sessions",
        ["project_slug"],
    )

    op.create_table(
        "dialogue_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dialogue_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "author IN ('customer', 'designer', 'director')",
            name="ck_dialogue_messages_author",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'delivered', 'rejected')",
            name="ck_dialogue_messages_status",
        ),
    )
    op.create_index(
        "ix_dialogue_messages_session_id",
        "dialogue_messages",
        ["session_id"],
    )
    op.create_index(
        "ix_dialogue_messages_session_created",
        "dialogue_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dialogue_messages_session_created", table_name="dialogue_messages")
    op.drop_index("ix_dialogue_messages_session_id", table_name="dialogue_messages")
    op.drop_table("dialogue_messages")

    op.drop_index("ix_dialogue_sessions_project_slug", table_name="dialogue_sessions")
    op.drop_index("ix_dialogue_sessions_user_id", table_name="dialogue_sessions")
    op.drop_table("dialogue_sessions")
