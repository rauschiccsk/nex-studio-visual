"""Add users.must_change_password — force a change of an admin-set initial password (v4.0.32).

An admin creates a user with an initial password; the user must change it on first login before
using the app. NOT NULL, server_default false so existing rows (seeded admin, legacy users) are
unaffected — they keep their password with no forced change. Set true on admin create + admin reset,
cleared when the user changes their OWN password.

Revision ID: 085
Revises: 084
Create Date: 2026-07-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
