"""Create pipeline_state + pipeline_message (F-007 Orchestration Cockpit, CR-NS-018 Phase 1).

Backend-owned single source of truth for the multi-agent pipeline:
* ``pipeline_state`` — one row per version (version_id UNIQUE): who is on
  turn and what's next.
* ``pipeline_message`` — append-only typed message log (the .dedo-channel
  replacement / queryable audit trail).

Enums are String + CHECK (codebase convention, no native PG ENUM).

Revision ID: 051
Revises: 050
Create Date: 2026-06-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAGES = "'kickoff', 'gate_a', 'gate_b', 'gate_c', 'gate_d', 'gate_e', 'build', 'gate_g', 'release', 'done'"
_ACTORS = "'coordinator', 'designer', 'customer', 'implementer', 'auditor', 'director'"
_PARTICIPANTS = "'coordinator', 'designer', 'customer', 'implementer', 'auditor', 'director', 'system'"


def upgrade() -> None:
    op.create_table(
        "pipeline_state",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("flow_type", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=16), nullable=False),
        sa.Column("current_actor", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_action", sa.Text(), server_default="", nullable=False),
        sa.Column("is_regate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("iteration", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "flow_type IN ('new_version', 'cr', 'bug')",
            name="ck_pipeline_state_flow_type",
        ),
        sa.CheckConstraint(
            f"current_stage IN ({_STAGES})",
            name="ck_pipeline_state_current_stage",
        ),
        sa.CheckConstraint(
            f"current_actor IN ({_ACTORS})",
            name="ck_pipeline_state_current_actor",
        ),
        sa.CheckConstraint(
            "status IN ('agent_working', 'awaiting_director', 'blocked', 'done')",
            name="ck_pipeline_state_status",
        ),
        sa.ForeignKeyConstraint(["version_id"], ["versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", name="uq_pipeline_state_version_id"),
    )

    op.create_table(
        "pipeline_message",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("author", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"stage IN ({_STAGES})",
            name="ck_pipeline_message_stage",
        ),
        sa.CheckConstraint(
            f"author IN ({_PARTICIPANTS})",
            name="ck_pipeline_message_author",
        ),
        sa.CheckConstraint(
            f"recipient IN ({_PARTICIPANTS})",
            name="ck_pipeline_message_recipient",
        ),
        sa.CheckConstraint(
            "kind IN ('kickoff', 'question', 'answer', 'gate_report', 'directive', "
            "'approval', 'return', 'verdict', 'notification')",
            name="ck_pipeline_message_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'answered', 'archived')",
            name="ck_pipeline_message_status",
        ),
        sa.ForeignKeyConstraint(["version_id"], ["versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pipeline_message_version_id",
        "pipeline_message",
        ["version_id"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_message_version_created",
        "pipeline_message",
        ["version_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_message_version_created", table_name="pipeline_message")
    op.drop_index("ix_pipeline_message_version_id", table_name="pipeline_message")
    op.drop_table("pipeline_message")
    op.drop_table("pipeline_state")
