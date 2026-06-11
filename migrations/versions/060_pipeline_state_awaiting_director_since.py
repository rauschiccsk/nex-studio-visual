"""Add ``pipeline_state.awaiting_director_since`` (WS-D metrics, CR-NS-036).

A nullable timestamp marking when the pipeline ENTERED its current Director-wait status
(``awaiting_director`` / ``blocked``). Maintained by a ``status`` ``set`` event listener on the ORM
model — stamped on entry, preserved across wait→wait, cleared on leaving. Powers the future metrics
page's "Director-wait time" (now − ``awaiting_director_since``). Nullable, defaults NULL; existing
rows keep NULL until their next status transition, so no behavioural change and no backfill (the
historical entry instants are unrecoverable — metrics start fresh, WS-D out-of-scope note).

Revision ID: 060
Revises: 059
Create Date: 2026-06-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pipeline_state",
        sa.Column("awaiting_director_since", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_state", "awaiting_director_since")
