"""Widen the pipeline stage CHECKs for the new ``vizual`` phase (CR-1, nex-studio-visual).

CR-1 inserts a live-preview phase ``vizual`` between ``navrh`` and ``programovanie`` (spec §3.A): the
AI-built frontend renders LIVE in the cockpit for the Manažér to walk + approve before Programovanie. The
canonical ``STAGE_VALUES`` tuple (``backend/db/models/pipeline.py``) grows accordingly, so the two DB stage
CHECK constraints that derive from it must widen in lock-step:

  * ``pipeline_state.ck_pipeline_state_current_stage`` — the live build position.
  * ``pipeline_message.ck_pipeline_message_stage``     — the append-only message log's phase stamp.

A CHECK-constraint value widening on the existing ``String`` columns (the codebase's String+CHECK
convention) — drop + re-add each CHECK with the widened ``IN`` list. No data migration, no new column.
Idempotent: DROP CONSTRAINT IF EXISTS. Mirrors migration 069 (the v2 4-phase stage rewrite) + 082.

Revision ID: 083
Revises: 082
Create Date: 2026-07-12

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "083"
down_revision: Union[str, None] = "082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Stage value sets (mirror ``backend/db/models/pipeline.py`` STAGE_VALUES). NEW adds ``'vizual'`` after
# ``'navrh'``; OLD is the pre-CR-1 v2 4-phase set (restored on downgrade).
_STAGE_NEW = "'priprava', 'navrh', 'vizual', 'programovanie', 'verifikacia', 'done'"
_STAGE_OLD = "'priprava', 'navrh', 'programovanie', 'verifikacia', 'done'"


def _rewrite(table: str, name: str, column: str, values: str) -> None:
    """Drop (IF EXISTS, for idempotency) and recreate a CHECK constraint with a new ``IN`` list."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.create_check_constraint(name, table, f"{column} IN ({values})")


def upgrade() -> None:
    _rewrite("pipeline_state", "ck_pipeline_state_current_stage", "current_stage", _STAGE_NEW)
    _rewrite("pipeline_message", "ck_pipeline_message_stage", "stage", _STAGE_NEW)


def downgrade() -> None:
    # Clear any rows carrying the retired ``vizual`` stage BEFORE re-validating the narrower CHECKs, so the
    # downgrade never fails on live data (a no-op on a DB that never entered the Vizuál phase).
    op.execute(f"DELETE FROM pipeline_message WHERE stage NOT IN ({_STAGE_OLD})")
    op.execute(f"DELETE FROM pipeline_state WHERE current_stage NOT IN ({_STAGE_OLD})")
    _rewrite("pipeline_state", "ck_pipeline_state_current_stage", "current_stage", _STAGE_OLD)
    _rewrite("pipeline_message", "ck_pipeline_message_stage", "stage", _STAGE_OLD)
