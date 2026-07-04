"""Add the nullable ``pipeline_state.mode`` column — the spine mode toggle (STEP 1, redesign Chrbtica).

The redesign's Riadiace centrum replaces the legacy 4-phase build automaton with a live 1:1 conversation
between the Manažér and the AI partner. To run the new conversation loop ALONGSIDE existing builds without
migrating anything, the runner (``pipeline_runner._run``) selects the loop by an ADDITIVE per-build flag:

  * ``pipeline_state.mode = 'conversation'`` → the spine conversation loop (``run_conversation_turn``).
  * ``pipeline_state.mode IS NULL`` (the default) → the phase automaton (``run_dispatch``) — UNCHANGED.

Net-new nullable ``VARCHAR(16)`` — purely additive, NO backfill (NULL is the correct "phase automaton"
default for every existing row, so every v2 PROD build is byte-identical), NO CHECK constraint (the value
set lives in one place in code — mirrors ``074_dial_override_columns`` ``miera_autonomie`` — so it evolves
without DDL churn). Does NOT touch the constrained ``current_stage`` / ``current_actor`` columns.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the column) never errors — mirrors 074 verbatim.

Revision ID: 079
Revises: 078
Create Date: 2026-07-04

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS mode VARCHAR(16)")


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_state DROP COLUMN IF EXISTS mode")
