"""Each account has its own colour (DEV-320).

**What happened.** The Director runs several cockpits side by side on one desktop, each account in its own Chrome
profile (07.11.2026: „aby pod jedným prihlásením spúšťať NSV pod rôznymi používateľmi“). The app window does not show
the profile's colour, so the windows looked alike.

**What it adds.** ``users.accent_color`` — one colour of ``ACCOUNT_COLORS`` (backend/config/settings.py). The accounts
that exist take the palette in the order they were created, as a new account takes the next one.

**Downgrade** drops the column.

Revision ID: 143
Revises: 142
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "143"
down_revision: Union[str, None] = "142"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The palette as it was when this revision was written — a migration does not follow later changes of the settings.
_PALETTE = ("#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#db2777", "#ca8a04")


def upgrade() -> None:
    op.add_column("users", sa.Column("accent_color", sa.String(7), nullable=True))
    palette = ", ".join(f"({i}, '{colour}')" for i, colour in enumerate(_PALETTE))
    op.execute(
        f"""
        UPDATE users SET accent_color = palette.colour
        FROM (
            SELECT id, (row_number() OVER (ORDER BY created_at, id) - 1) % {len(_PALETTE)} AS slot FROM users
        ) AS ranked
        JOIN (VALUES {palette}) AS palette(slot, colour) ON palette.slot = ranked.slot
        WHERE users.id = ranked.id
        """
    )


def downgrade() -> None:
    op.drop_column("users", "accent_color")
