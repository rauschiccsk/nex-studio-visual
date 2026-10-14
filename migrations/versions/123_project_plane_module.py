"""The module of the project's application in Plane's DEV register, kept by id (DEV-117).

A renamed project keeps its tickets.

Revision ID: 123
Revises: 122
Create Date: 2026-10-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "123"
down_revision: Union[str, None] = "122"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("plane_module_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "plane_module_id")
