"""A Poradca conversation remembers the phase whose decision cards it reviews (DEV-138).

The „Overiť s Poradcom“ button founded a new conversation for every card — Návrh of NEX Inbox 1.9.0 has nine. The
conversation the button founds now keeps the phase, and the next card of the same version and phase goes into it.
Conversations started before this column (and every hand-written one) keep ``NULL`` and are never taken over.

Revision ID: 129
Revises: 128
Create Date: 2026-10-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "129"
down_revision: Union[str, None] = "128"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Stage values (mirror ``backend/db/models/pipeline.py`` STAGE_VALUES at revision 129, frozen here as in 083 — a
# migration must keep meaning what it meant when it ran).
_STAGES = "'priprava', 'navrh', 'vizual', 'programovanie', 'verifikacia', 'done'"


def upgrade() -> None:
    op.add_column("poradca_conversations", sa.Column("review_stage", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_poradca_conversations_review_stage",
        "poradca_conversations",
        f"review_stage IS NULL OR review_stage IN ({_STAGES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_poradca_conversations_review_stage", "poradca_conversations", type_="check")
    op.drop_column("poradca_conversations", "review_stage")
