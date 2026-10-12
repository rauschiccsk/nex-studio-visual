"""A version may have its own token rates for its delivery statement, and every change of them is kept (DEV-74).

12.10.2026 the Director: the rates in Nastavenia stay the default, but for a particular version he wants to set other
ones. Empty = the rate from Nastavenia, as before. An issued statement keeps the rates it was issued with, so a change
here never alters it — only a new statement that replaces the valid one uses the new rates.

Revision ID: 117
Revises: 116
Create Date: 2026-10-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "117"
down_revision: Union[str, None] = "116"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("rate_code", sa.Numeric(12, 4), nullable=True))
    op.add_column("versions", sa.Column("rate_docs", sa.Numeric(12, 4), nullable=True))
    # NULL passes (= the rate in Nastavenia); a set rate is a price, so above zero.
    op.create_check_constraint("ck_versions_rates_positive", "versions", "rate_code > 0 AND rate_docs > 0")
    op.create_table(
        "version_rate_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_rate_code", sa.Numeric(12, 4), nullable=True),
        sa.Column("previous_rate_docs", sa.Numeric(12, 4), nullable=True),
        sa.Column("rate_code", sa.Numeric(12, 4), nullable=True),
        sa.Column("rate_docs", sa.Numeric(12, 4), nullable=True),
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        # clock_timestamp(): the moment of the change, not the start of its transaction.
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
    )
    op.create_index("ix_version_rate_changes_version_id", "version_rate_changes", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_version_rate_changes_version_id", table_name="version_rate_changes")
    op.drop_table("version_rate_changes")
    op.drop_constraint("ck_versions_rates_positive", "versions", type_="check")
    op.drop_column("versions", "rate_docs")
    op.drop_column("versions", "rate_code")
