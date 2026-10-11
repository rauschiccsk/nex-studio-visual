"""A deploy whose new version was started but whose check after it failed is recorded as ``unverified`` (DEV-68).

11.10.2026, NEX Inbox 1.7.1 on the customer's PROD: the new containers ran and the database was migrated when the last
check failed. Recorded as ``failed``, the matrix kept showing 1.5.6 as the version in PROD — the version that no
longer ran. ``unverified`` keeps the failure visible and the running version true.

Revision ID: 115
Revises: 114
Create Date: 2026-10-11

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "115"
down_revision: Union[str, None] = "114"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "deploy_events"
_CHECK = "ck_deploy_events_status"


def upgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "status IN ('ok', 'failed', 'unverified')")


def downgrade() -> None:
    # An unverified deploy started its version but did not pass its checks — before this revision that was a failure.
    op.execute(f"UPDATE {_TABLE} SET status = 'failed' WHERE status = 'unverified'")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "status IN ('ok', 'failed')")
