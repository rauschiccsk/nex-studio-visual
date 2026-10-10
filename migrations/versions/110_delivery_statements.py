"""Delivered-token statements of versions and the work kind of a version (DEV-50).

* ``versions.work_kind`` — ``fix`` (a fix of an error in code we delivered: the code does not do what the approved
  specification says — never billed) or ``change`` (new work or a change — billed). NULL = not decided; a fast fix
  must be decided before its statement is issued (the cockpit never guesses it), a new version counts as ``change``.
* ``delivery_statements`` — an ISSUED statement: what a version delivered between two commits, counted with the
  pinned tokenizer, with the rates it was issued at. Rows are never rewritten — a later change of the rates or of
  the counting rule must not change an invoice already sent; a new issue is a new row.

Revision ID: 110
Revises: 109
Create Date: 2026-10-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "110"
down_revision: Union[str, None] = "109"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("work_kind", sa.String(10), nullable=True))
    op.create_check_constraint(
        "ck_versions_work_kind", "versions", "work_kind IS NULL OR work_kind IN ('fix', 'change')"
    )
    op.create_table(
        "delivery_statements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("delivered_sha", sa.String(40), nullable=False),
        sa.Column("delivered_source", sa.String(40), nullable=False),
        sa.Column("tokenizer", sa.String(100), nullable=False),
        sa.Column("work_kind", sa.String(10), nullable=False),
        sa.Column("tokens_code", sa.Integer(), nullable=False),
        sa.Column("tokens_tests", sa.Integer(), nullable=False),
        sa.Column("tokens_docs", sa.Integer(), nullable=False),
        sa.Column("rate_code", sa.Numeric(12, 4), nullable=False),
        sa.Column("rate_docs", sa.Numeric(12, 4), nullable=False),
        sa.Column("amount_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("work_kind IN ('fix', 'change')", name="ck_delivery_statements_work_kind"),
        sa.CheckConstraint(
            "tokens_code >= 0 AND tokens_tests >= 0 AND tokens_docs >= 0 AND amount_eur >= 0",
            name="ck_delivery_statements_nonneg",
        ),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delivery_statements")
    op.execute("ALTER TABLE versions DROP CONSTRAINT IF EXISTS ck_versions_work_kind")
    op.execute("ALTER TABLE versions DROP COLUMN IF EXISTS work_kind")
