"""Add ``projects.uat_slug`` — the UAT deploy mapping (F-009, CR-NS-098).

The Fast-Fix Lane auto-redeploys a project's UAT after a clean fast-fix release so the Director SEES
the fix running before the single ``uat_accept`` touch. ``uat_slug`` maps a project to its
``/opt/uat/<uat_slug>`` deploy (e.g. ``nex-ledger`` → ``"ledger"``, ``nex-inbox`` → ``"mager"``);
NULL = no UAT configured → the auto-deploy is skipped. Nullable ``VARCHAR(100)`` — purely additive,
no backfill, no constraint.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the column) never errors.

Revision ID: 065
Revises: 064
Create Date: 2026-06-16

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS uat_slug VARCHAR(100)")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS uat_slug")
