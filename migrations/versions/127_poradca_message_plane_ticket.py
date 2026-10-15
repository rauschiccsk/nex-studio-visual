"""A Poradca answer remembers the ticket in Plane a person founded from it (DEV-119).

Poradca proposes a ticket — a problem of the cockpit for Andros Dedo, or work on the project — and a person's click
founds it; the answer keeps the ticket, so a second click returns it instead of founding another.

Revision ID: 127
Revises: 126
Create Date: 2026-10-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "127"
down_revision: Union[str, None] = "126"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_messages", sa.Column("plane_issue_id", sa.String(64), nullable=True))
    op.add_column("poradca_messages", sa.Column("plane_identifier", sa.String(32), nullable=True))
    op.add_column("poradca_messages", sa.Column("plane_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("poradca_messages", "plane_url")
    op.drop_column("poradca_messages", "plane_identifier")
    op.drop_column("poradca_messages", "plane_issue_id")
