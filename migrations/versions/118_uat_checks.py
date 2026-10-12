"""What the manager tried in UAT — per customer, version and change of the test guide (DEV-91).

12.10.2026 the Director wants to tick off what he tested in UAT, see at „Akceptovať“ how much of it, and turn a
change that does not work into a fast fix. A row per (customer, version, change): tried, or does not work with a note;
who and when. The change is named by its title in the guide (or its text in the release note of an older version).

Revision ID: 118
Revises: 117
Create Date: 2026-10-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "118"
down_revision: Union[str, None] = "117"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uat_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("checked_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('tested', 'broken')", name="ck_uat_checks_status"),
        # Also the index for reading one customer's checks of a version — its leading columns.
        sa.UniqueConstraint("customer_id", "version_id", "item", name="uq_uat_checks_item"),
    )


def downgrade() -> None:
    op.drop_table("uat_checks")
