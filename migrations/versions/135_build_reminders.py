"""When a waiting build last reminded the Manažér on Telegram (DEV-152).

Kept in the database, so a restart of the cockpit never sends a reminder twice in one interval.

Revision ID: 135
Revises: 134
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "135"
down_revision: Union[str, None] = "134"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_state", sa.Column("last_reminder_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_state", "last_reminder_at")
