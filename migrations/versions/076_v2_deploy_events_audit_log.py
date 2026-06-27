"""Create the per-customer deploy & UAT-acceptance audit-log table (v2.0.0, CR-V2-026).

Design source: ``docs/architecture/nex-studio-v2-design.md`` §3 (Deploy &
Customers) — §3.5 (the per-customer UAT acceptance gate is logged: who / when /
version / customer, never bypassed) + the build plan CR-V2-026 (DEPLOY-6/8/9/10).

A ``deploy_events`` row is the append-only audit trail of every deploy and every
acceptance, per customer. It is the source of truth the deploy backend consults
to enforce the never-bypassed UAT acceptance gate (no PROD deploy for a (version,
customer) without a recorded ``accept`` for that exact pair).

Secret governance (OQ-5 / CLAUDE.md §4/§5): this table NEVER stores secret
material — per-customer secrets live only in the credentials store; the deploy
backend points into it. The ``detail`` column carries only a non-secret summary.

``ON DELETE CASCADE`` on ``customer_id`` / ``project_id`` removes a customer's /
project's deploy history when they are deleted (the log is meaningless without
them). ``ON DELETE SET NULL`` on ``actor_id`` keeps the history if a user is
removed.

NB on the revision number: the build plan §6 migration table aspirationally
reserved "074" for this audit-log. The repo enforces strictly CONTIGUOUS
numbering (``test_alembic_migrations.test_migration_files_form_contiguous_chain``)
and the migration NUMBER is a label — only ``down_revision`` is load-bearing —
so this takes the next contiguous number **076** chaining after the current head
**075** (customers registry). The plan-vs-repo numbering is reconciled by the
contiguous-chain rule, not by the aspirational table.

Idempotent: ``IF NOT EXISTS`` / ``IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the table) never errors.

Revision ID: 076
Revises: 075
Create Date: 2026-06-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deploy_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Monotonic append-order sequence — the deterministic "latest event" key
        # (created_at is a transaction-start timestamp, identical within a txn).
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.String(length=50), nullable=False),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("event_type", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="ok", nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
            name="fk_deploy_events_customer_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_deploy_events_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_deploy_events_actor_id",
        ),
        sa.CheckConstraint("environment IN ('uat', 'prod')", name="ck_deploy_events_environment"),
        sa.CheckConstraint("event_type IN ('deploy', 'accept')", name="ck_deploy_events_event_type"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_deploy_events_status"),
        sa.UniqueConstraint("seq", name="uq_deploy_events_seq"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_deploy_events_customer_id",
        "deploy_events",
        ["customer_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_deploy_events_project_id",
        "deploy_events",
        ["project_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_deploy_events_project_id", table_name="deploy_events", if_exists=True)
    op.drop_index("ix_deploy_events_customer_id", table_name="deploy_events", if_exists=True)
    op.drop_table("deploy_events", if_exists=True)
