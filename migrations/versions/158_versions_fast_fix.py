"""A version knows it is a fast fix from its founding on (DEV-492).

**What happened.** 22.11.2026: a fast fix was founded and started in one click, from the project page or from a ticket,
so a version learned it was a fast fix only when its build started (``pipeline_state.flow_type``). The Director:
„Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“ A fast fix is now founded first, takes
its tickets like any version, and starts later — so the version has to know what it is before it has a build.

**What it changes.** Adds ``versions.fast_fix`` (default false) and marks every version whose build runs as a fast fix.

**Downgrade** drops the column.

Revision ID: 158
Revises: 157
Create Date: 2026-11-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "158"
down_revision: Union[str, None] = "157"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("fast_fix", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        "UPDATE versions SET fast_fix = true "
        "WHERE id IN (SELECT version_id FROM pipeline_state WHERE flow_type = 'fast_fix')"
    )


def downgrade() -> None:
    op.drop_column("versions", "fast_fix")
