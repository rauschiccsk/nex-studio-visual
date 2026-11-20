"""The number a version had before its first-PROD graduation (DEV-449).

**What happened.** 20.11.2026 Orthodox Hermenia: the graduation to 1.0.0 ran after the first PROD deploy and its commit
went to main unchecked. It now runs when the Manažér accepts the UAT that opens the first PROD — the screens he looked
at still name the old number.

**What it changes.** Adds ``versions.graduated_from``: the number the version carried before the graduation, so the
deploy and the acceptance gate still find it under the number the Manažér saw; and ``versions.graduation_commit``: the
commit that moved its release notes to 1.0.0 — the commit the first PROD builds (its CI must be green first).

**Downgrade** drops the columns.

Revision ID: 153
Revises: 152
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "153"
down_revision: Union[str, None] = "152"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("graduated_from", sa.String(50), nullable=True))
    op.add_column("versions", sa.Column("graduation_commit", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("versions", "graduation_commit")
    op.drop_column("versions", "graduated_from")
