"""Widen pipeline_state.status to VARCHAR(20) (F-007, CR-NS-018 Phase 2).

Migration 051 created ``pipeline_state.status`` as VARCHAR(16), but the value
``'awaiting_director'`` is 17 chars. 051 was already applied (Phase 1 deploy),
so this is a separate ALTER rather than an edit to 051.

Revision ID: 054
Revises: 053
Create Date: 2026-06-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "pipeline_state",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "pipeline_state",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
