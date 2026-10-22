"""Orientačná cena verzie — measurements of finished versions and the estimate given for a version (DEV-154).

Director 22.10.2026: „Súhlasím“ — the estimate comes after Návrh, from the number of tasks in the plan.

Revision ID: 136
Revises: 135
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "136"
down_revision: Union[str, None] = "135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_samples",
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("first_version", sa.Boolean(), nullable=False),
        sa.Column("tasks", sa.Integer(), nullable=False),
        sa.Column("code_tokens", sa.Integer(), nullable=False),
        sa.Column("docs_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "measured_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
        ),
    )
    op.create_table(
        "price_estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tasks", sa.Integer(), nullable=False),
        sa.Column("first_version", sa.Boolean(), nullable=False),
        sa.Column("code_tokens", sa.Integer(), nullable=False),
        sa.Column("docs_tokens", sa.Integer(), nullable=False),
        sa.Column("rate_code", sa.Numeric(12, 4), nullable=False),
        sa.Column("rate_docs", sa.Numeric(12, 4), nullable=False),
        sa.Column("amount_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("spread_pct", sa.Integer(), nullable=True),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
        ),
    )
    # One estimate per version — the unique index is the lookup too.
    op.create_index("ix_price_estimates_version_id", "price_estimates", ["version_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_price_estimates_version_id", table_name="price_estimates")
    op.drop_table("price_estimates")
    op.drop_table("price_samples")
