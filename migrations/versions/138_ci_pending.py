"""The version waits for the CI of its last commit in its state, not in a turn (DEV-201).

23.10.2026 NEX Inbox 1.10.1 passed Verifikácia „bez výsledku CI“: the gate waited 20 minutes inside the Auditor's turn,
the CI took 49 (its jobs waited for the only runner) and failed. ``pipeline_state.ci_pending_since`` — since when a
version passed by the Auditor waits for that CI; the cockpit's CI watcher settles it. Director 23.10.2026: „Áno,
súhlasím, implementuj DEV-201“.

Revision ID: 138
Revises: 137
Create Date: 2026-10-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "138"
down_revision: Union[str, None] = "137"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_state", sa.Column("ci_pending_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_state", "ci_pending_since")
