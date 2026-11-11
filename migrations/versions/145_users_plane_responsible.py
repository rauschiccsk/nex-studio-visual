"""The account's responsible person in Plane (DEV-360).

**What happened.** The ticket of a project founded from an idea names its responsible person from the „Zodpovedný“
group of the DEV register. The cockpit found the person by the account's names only. The Director's accounts alex
(„Alexander Honest“) and rausch („Develop Rausch“) match nobody in Plane, so on 11.11.2026 the ticket of NEX Archive
was never founded. Director: „Súhlasím, založ tiket a sprav to.“

**What it changes.** Adds ``users.plane_responsible`` — the name of the person of the „Zodpovedný“ group the Manažér
chose for the account; ``NULL`` keeps matching the account by its names.

**Downgrade** drops the column; every account is matched by its names again.

Revision ID: 145
Revises: 144
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "145"
down_revision: Union[str, None] = "144"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plane_responsible", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "plane_responsible")
