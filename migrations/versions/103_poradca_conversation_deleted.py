"""Deleted Poradca conversation — text gone, price stays (ICCINT-167, v4.42.0).

**What happened.** A Poradca conversation could be neither renamed nor removed; the list on the left only grew. Director
05.10.2026: „chýba mi premenovanie rozhovoru a vymazanie rozhovoru."

**Why not delete the rows.** The project's Náklady sum the spend of Poradca answers from ``poradca_messages``
(``metrics._poradca_rows``). Deleted rows would take away money that was really spent. So
deletion discards everything said in the conversation (questions, answers, steps, errors, title, session log
on disk), keeps only the spend and time, and marks the conversation ``deleted_at`` — it vanishes from list and UI.

**Downgrade** drops the column; deleted conversations would then show in the list empty with the title
„Vymazaný rozhovor" — nothing of their content returns, because nothing of it was left.

Revision ID: 103
Revises: 102
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "103"
down_revision: Union[str, None] = "102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("poradca_conversations", "deleted_at")
