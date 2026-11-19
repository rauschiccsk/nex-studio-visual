"""Who saw which stored customer secret, and when (DEV-437).

**What happened.** 19.11.2026 the Director wants the customer card to show stored secrets as asterisks, with an eye at
the end of the line that shows the secret — „toto bude mať len Administrátor“. Showing a secret is recorded.

**What it changes.** Adds ``customer_secret_reveals``: the customer, the variable shown, who showed it and when — never
the value.

**Downgrade** drops the table.

Revision ID: 151
Revises: 150
Create Date: 2026-11-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "151"
down_revision: Union[str, None] = "150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_secret_reveals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_secret_reveals_customer_id", "customer_secret_reveals", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_secret_reveals_customer_id", table_name="customer_secret_reveals")
    op.drop_table("customer_secret_reveals")
