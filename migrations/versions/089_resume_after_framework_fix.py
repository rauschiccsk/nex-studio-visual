"""Add ``pipeline_state.resume_after_framework_fix`` — the trigger that restarts a fixed build (ICCINT-13).

A build that escalated a NEX Studio bug settles ``blocked`` / ``block_reason='framework_issue'`` and, until
now, stayed there FOREVER: the Manažér's only action was ``nahlasit_znova`` (re-send the report), which
changes no state and dispatches no turn. Once Dedo fixed NEX Studio there was nothing to press.

This column is that missing trigger. Dedo's host-side unblock
(:func:`backend.services.dedo_unblock.unblock_framework_issue`) sets it True while settling the build back to
``awaiting_manazer``; ``determine_available_actions`` — which is state-only BY DESIGN, hence a column rather
than a derived query — then offers exactly one action, ``pokracovat``, in whichever phase the escalation
happened. The status listener clears it the moment a turn starts.

NOT NULL with a ``false`` server default, so every existing row is correct without a backfill (no build is
mid-unblock at migration time — the flag lives only between Dedo's command and the Manažér's click).

Revision ID: 089
Revises: 088
Create Date: 2026-08-22

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "089"
down_revision: Union[str, None] = "088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent ADD COLUMN IF NOT EXISTS — mirrors migration 066, which added the sibling boolean
    # ``pipeline_state.dispatch_in_flight`` the same way.
    op.execute(
        "ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS resume_after_framework_fix BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_state DROP COLUMN IF EXISTS resume_after_framework_fix")
