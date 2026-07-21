"""Add versions.vizual_approved_sha — the approved-Vizuál commit (v4.0.23 binding).

The git commit of the Manažér-approved Vizuál ``frontend/`` is the binding contract:
Programovanie preserves it (wire data, don't redesign) and the Auditor verifies the delivered
FE still matches it. Nullable — NULL until a version's Vizuál is approved.

Revision ID: 084
Revises: 083
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "084"
down_revision: Union[str, None] = "083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("vizual_approved_sha", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("versions", "vizual_approved_sha")
