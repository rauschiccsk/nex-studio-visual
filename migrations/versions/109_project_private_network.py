"""Projects reachable only from the private network (DEV-42).

``projects.private_network`` (bool, default false) — the project's installations and Vizuál preview get a name in
the private zone ``*.int.isnex.eu`` (ANDROS in Tailscale, ICCINT-213) and no public one. Career Asistent keeps
personal data of candidates and its specification promises access only from our private network; until now the
cockpit published every installation on the public ``*.isnex.eu``.

A new NOT NULL column with a server default — existing projects stay public, as before. Idempotent downgrade.

Revision ID: 109
Revises: 108
Create Date: 2026-10-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "109"
down_revision: Union[str, None] = "108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("private_network", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS private_network")
