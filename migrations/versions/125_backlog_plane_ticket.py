"""A backlog requirement remembers its ticket in Plane, and a rejected one its reason (DEV-121).

The requirement and the ticket stay linked — the requirement knows the ticket's id, identifier and address, the
ticket names REQ-N in its text — so a second click returns the existing ticket and the ticket follows the
requirement on its own: included in a version, deployed to PROD, rejected with the reason written here.

Revision ID: 125
Revises: 124
Create Date: 2026-10-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "125"
down_revision: Union[str, None] = "124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("backlog_items", sa.Column("plane_issue_id", sa.String(64), nullable=True))
    op.add_column("backlog_items", sa.Column("plane_identifier", sa.String(32), nullable=True))
    op.add_column("backlog_items", sa.Column("plane_url", sa.String(500), nullable=True))
    op.add_column("backlog_items", sa.Column("rejection_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("backlog_items", "rejection_reason")
    op.drop_column("backlog_items", "plane_url")
    op.drop_column("backlog_items", "plane_identifier")
    op.drop_column("backlog_items", "plane_issue_id")
