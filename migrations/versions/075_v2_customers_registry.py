"""Create the per-project customers registry table (v2.0.0, CR-V2-025).

Design source: ``docs/architecture/nex-studio-v2-design.md`` §3.2 ("Zákazníci")
+ build plan CR-V2-025 (DEPLOY-1..3). A ``customers`` row is a project-scoped
registry entry; each customer runs the app on its own UAT + PROD instance / DB /
data (the instance-per-customer model).

Secret governance (OQ-5 / CLAUDE.md §4/§5): this table NEVER stores secret
material. ``credential_id`` is a nullable POINTER into the existing credentials
store (table ``credentials``, created in migration 038); the secret VALUE lives
only in the ``ri``-gated credentials registry / on-disk file. ``ON DELETE SET
NULL`` keeps a customer intact if its credentials row is removed.

``ON DELETE CASCADE`` on ``project_id`` removes a project's customers when the
project is deleted (the registry is meaningless without the project).

NB on the revision number: the build plan §6 migration table aspirationally
reserved "073" for this migration. The repo enforces strictly CONTIGUOUS
numbering (``test_alembic_migrations.test_migration_files_form_contiguous_chain``)
and the migration NUMBER is a label — only ``down_revision`` is load-bearing —
so this takes the next contiguous number **075** chaining after the current head
**074** (dial override columns). The plan-vs-repo numbering is reconciled by the
contiguous-chain rule, not by the aspirational table.

Idempotent: ``IF NOT EXISTS`` / ``IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the table) never errors.

Revision ID: 075
Revises: 074
Create Date: 2026-06-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("subdomain", sa.String(length=255), nullable=True),
        sa.Column("integrations", JSONB(), nullable=True),
        sa.Column("credential_id", UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_customers_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="SET NULL",
            name="fk_customers_credential_id",
        ),
        sa.UniqueConstraint("project_id", "slug", name="uq_customers_project_slug"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_customers_project_id",
        "customers",
        ["project_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_customers_project_id", table_name="customers", if_exists=True)
    op.drop_table("customers", if_exists=True)
