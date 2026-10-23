"""Drop the columns and the table nothing reads (DEV-155, group B).

Director 20.10.2026: „hĺbkovú kontrolu mrtvých súborov a kódu, všetko to, čo projekt obsahuje a vôbec sa nepoužíva.“
23.10.2026 to group B — six columns and the bug register: „Súhlasím“. Measured on the live cockpit database and in the
code: no reader for any of them; the bug register lost its last writer with the first version's API (DEV-155, group
A), and its only readers were a count of zero on the version page and an always-empty „Opravené“ block.

* ``pipeline_state.returns_to`` — the retired v1 Coordinator's return marker, never written in v2.
* ``feats.task_count`` — never written, never read.
* ``feats.actual_minutes``, ``tasks.actual_minutes`` — written only by the removed routes.
* ``agent_terminal_sessions.last_activity_at`` — never written, never read.
* ``price_samples.measured_at`` — added by DEV-154, read by nothing.
* table ``bugs`` — no table references it since migration 048.

Revision ID: 137
Revises: 136
Create Date: 2026-10-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "137"
down_revision: Union[str, None] = "136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("pipeline_state", "returns_to")
    op.drop_column("feats", "task_count")
    op.drop_column("feats", "actual_minutes")
    op.drop_column("tasks", "actual_minutes")
    op.drop_column("agent_terminal_sessions", "last_activity_at")
    op.drop_column("price_samples", "measured_at")
    op.drop_table("bugs")


def downgrade() -> None:
    op.create_table(
        "bugs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bug_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column("source", sa.String(length=20), server_default="internal", nullable=False),
        sa.Column("reported_by", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=50), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("commit_hash", sa.String(length=40), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("severity IN ('critical', 'major', 'minor')", name="ck_bugs_severity"),
        sa.CheckConstraint("source IN ('internal', 'customer')", name="ck_bugs_source"),
        sa.CheckConstraint(
            "status IN ('new', 'accepted', 'in_progress', 'resolved', 'wont_fix')",
            name="ck_bugs_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id"], ["versions.id"], name="fk_bugs_version_id_versions", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "bug_number", name="uq_bugs_project_id_bug_number"),
    )
    op.create_index("ix_bugs_project_id", "bugs", ["project_id"], unique=False)
    op.create_index("ix_bugs_severity", "bugs", ["severity"], unique=False)
    op.create_index("ix_bugs_status", "bugs", ["status"], unique=False)
    op.create_index("ix_bugs_version_id", "bugs", ["version_id"], unique=False)
    op.add_column(
        "price_samples",
        sa.Column(
            "measured_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
        ),
    )
    op.add_column(
        "agent_terminal_sessions",
        sa.Column("last_activity_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column("tasks", sa.Column("actual_minutes", sa.Integer(), nullable=True))
    op.add_column("feats", sa.Column("actual_minutes", sa.Integer(), nullable=True))
    op.add_column("feats", sa.Column("task_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("pipeline_state", sa.Column("returns_to", sa.String(length=20), nullable=True))
