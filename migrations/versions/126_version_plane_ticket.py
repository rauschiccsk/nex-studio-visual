"""A version remembers the ticket in Plane it was started from (DEV-122).

A fast fix started from a ticket in the „Plane tikety" tab and the ticket stay linked, so the ticket follows the
version on its own: started → In Progress, deployed to UAT → a comment, deployed to PROD → Done.

Revision ID: 126
Revises: 125
Create Date: 2026-10-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "126"
down_revision: Union[str, None] = "125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("plane_issue_id", sa.String(64), nullable=True))
    op.add_column("versions", sa.Column("plane_identifier", sa.String(32), nullable=True))
    op.add_column("versions", sa.Column("plane_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("versions", "plane_url")
    op.drop_column("versions", "plane_identifier")
    op.drop_column("versions", "plane_issue_id")
