"""The languages an app speaks and the language an installation starts in (DEV-395).

**What happened.** 15.11.2026 the Director wanted Orthodox Hermenia's screens in Hungarian; the cockpit built only
Slovak apps. Director: „Viacjazyčné aplikácie budeme potrebovať … každý užívateľ môže nastaviť pre seba jazyk
aplikácie.“ To the direction and the tickets: „Súhlasím“, „Áno založ všetky štyri a začni ich implementovať.“

**What it changes.** Adds ``projects.languages`` — the languages the project's app speaks, the main one first (a JSON
list of codes; every existing project speaks Slovak, as before) — and ``customers.language`` — the language the
customer's installations start in, ``NULL`` = the app's main language.

**Downgrade** drops both columns.

Revision ID: 147
Revises: 146
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "147"
down_revision: Union[str, None] = "146"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("languages", postgresql.JSONB(), nullable=False, server_default='["sk"]'),
    )
    op.add_column("customers", sa.Column("language", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "language")
    op.drop_column("projects", "languages")
