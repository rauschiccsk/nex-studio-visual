"""A build a restart of the cockpit cut off can go on in whichever phase it was (DEV-204).

23.10.2026 13:18 UTC the deploy of the cockpit 4.84.0 restarted the backend while NEX Inbox 1.10.2 was being verified.
The recovery told the Manažér „Pokračuj“, but „Pokračovať“ was offered only in a paused Programovanie — the build had
no way on. ``pipeline_state.resume_after_restart`` marks such a build; a build the recovery settled before this column
existed is marked here, when nothing happened to it since (its newest message is still the recovery's). Director:
„Áno, súhlasím, založ tiket a oprav to“.

Revision ID: 139
Revises: 138
Create Date: 2026-10-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "139"
down_revision: Union[str, None] = "138"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: A build waiting for the Manažér whose newest message is the recovery's note about its current phase.
MARK_RECOVERED = """
UPDATE pipeline_state AS ps SET resume_after_restart = true
WHERE ps.status = 'awaiting_manazer'
  AND EXISTS (
    SELECT 1 FROM (
      SELECT pm.payload FROM pipeline_message AS pm WHERE pm.version_id = ps.version_id ORDER BY pm.seq DESC LIMIT 1
    ) AS newest
    WHERE newest.payload ->> 'recovery_audit' = 'true' AND newest.payload ->> 'phase' = ps.current_stage
  )
"""


def upgrade() -> None:
    op.add_column(
        "pipeline_state", sa.Column("resume_after_restart", sa.Boolean(), nullable=False, server_default="false")
    )
    op.execute(MARK_RECOVERED)


def downgrade() -> None:
    op.drop_column("pipeline_state", "resume_after_restart")
