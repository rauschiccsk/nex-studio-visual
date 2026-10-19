"""A brief from Dedo carries the version number it proposes (DEV-149).

19.10.2026 Dedo's brief for NEX Inbox proposed 1.10.0 in a sentence; the cockpit could not read it and the Manažér's
click founded 2.0.0. The number now travels as data, the Manažér sees it before founding and may change it. Briefs
from before this column keep ``NULL`` — the cockpit suggests the number for them as before.

Revision ID: 130
Revises: 129
Create Date: 2026-10-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "130"
down_revision: Union[str, None] = "129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dedo_project_proposal", sa.Column("proposed_version_number", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("dedo_project_proposal", "proposed_version_number")
