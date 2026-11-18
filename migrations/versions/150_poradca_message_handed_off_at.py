"""When a Poradca answer was put into Riadiace centrum (DEV-431).

**What happened.** 18.11.2026 the Director: „Ak som v záložke Poradca a je na obrazovke tlačidlo "Vložiť do Riadiaceho
centra" nevidím tu, či som to už urobil alebo nie.“ Putting an answer into Riadiace centrum went only through the
browser's storage; the answer kept no record of it.

**What it changes.** Adds ``poradca_messages.handed_off_at`` — when the answer (its instruction, its answer for the
agent or the green choice of a card) was last put into Riadiace centrum. ``NULL`` for every existing answer.

**Downgrade** drops the column.

Revision ID: 150
Revises: 149
Create Date: 2026-11-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "150"
down_revision: Union[str, None] = "149"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_messages", sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("poradca_messages", "handed_off_at")
