"""A deploy event keeps its warnings in a field of their own, not glued into its text (DEV-129).

The page said every warning of a deploy twice — once split back out of the event's text, behind an English
„warnings:“, and once from the click's answer. Events recorded before this column keep ``[]`` and are split when read;
the audit log is not rewritten.

Revision ID: 128
Revises: 127
Create Date: 2026-10-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "128"
down_revision: Union[str, None] = "127"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deploy_events",
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("deploy_events", "warnings")
