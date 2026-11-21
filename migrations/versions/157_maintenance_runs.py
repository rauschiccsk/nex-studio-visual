"""When a periodic maintenance job last ran (DEV-484).

**What happened.** 21.11.2026: the daily pruning of the CI runners' Docker (DEV-435) slept a day after every start of
the backend; the cockpit was deployed several times a day, and in 72 hours the pruning never ran.

**What it changes.** Adds ``maintenance_runs`` — a job's name and when it last ran; the loop waits only for what is
left of its interval.

**Downgrade** drops the table.

Revision ID: 157
Revises: 156
Create Date: 2026-11-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "157"
down_revision: Union[str, None] = "156"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maintenance_runs",
        sa.Column("job", sa.String(64), primary_key=True),
        sa.Column("last_run_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("maintenance_runs")
