"""When the Staviteľ got the requirement (DEV-361).

**What happened.** The Staviteľ got the Zásobník's requirements only in the opening brief of Príprava (DEV-134). A
requirement included in a version whose Príprava already ran never reached it — 11.11.2026 Dominik's addition to
orthodox-register 0.1.0 would have been „included“ and later „realized“ without anyone building it. Director: on the
finding and the proposal, „Súhlasím“.

**What it changes.** Adds ``backlog_items.delivered_at`` — when the requirement went to the Staviteľ in a round of
Príprava; ``NULL`` — not yet, so the next round of Príprava carries it. On 11.11.2026 no requirement was included in
any version, so no row needs a value.

**Downgrade** drops the column.

Revision ID: 146
Revises: 145
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "146"
down_revision: Union[str, None] = "145"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("backlog_items", sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("backlog_items", "delivered_at")
