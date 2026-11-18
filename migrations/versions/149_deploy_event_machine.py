"""The machine a deploy went to (DEV-404).

**What happened.** 16.11.2026: the „Inštalácie“ page showed Career Asistent PROD of ICC s.r.o. as 1.0.0 on MAGER — it
was never there. Its one PROD deploy of 15.10.2026 went to ANDROS; the page took the machine from the customer's setting
of today (``customers.prod_host``), which moved to MAGER on 31.10. A deploy did not record where it went. Director
16.11.2026: „Súhlasím, založ, ale teraz nerob.“; 18.11.2026 in the batch, older records as they are.

**What it changes.** Adds ``deploy_events.machine`` — the machine a deploy went to: ``""`` this machine, otherwise the
machine's value (``mager``). ``NULL`` for every existing event and for an acceptance: not recorded — the page says so,
nothing is guessed back (a mass change of the history is the Director's, and he chose to leave it).

**Downgrade** drops the column.

Revision ID: 149
Revises: 148
Create Date: 2026-11-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "149"
down_revision: Union[str, None] = "148"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deploy_events", sa.Column("machine", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("deploy_events", "machine")
