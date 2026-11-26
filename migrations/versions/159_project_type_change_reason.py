"""Adding the web frontend keeps why (DEV-522).

**What happened.** 26.11.2026: the project page of orthodox-register offered „Prepnúť na Web“ to whoever operated the
project, in a box the Director read as the project's setting. The Director: „Je neprípustné, aby typ projektu a ďaľšie
jeho parametre, ktoré určíme pri zakladaní projektu boli zmenené počas tvorby projektu.“ Adding the web frontend is now
the one exception — by the admin account, before the project's first PROD deploy, with a written reason.

**What it changes.** Adds ``project_type_changes.reason`` (text); earlier changes have none.

**Downgrade** drops the column.

Revision ID: 159
Revises: 158
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "159"
down_revision: Union[str, None] = "158"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_type_changes", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_type_changes", "reason")
