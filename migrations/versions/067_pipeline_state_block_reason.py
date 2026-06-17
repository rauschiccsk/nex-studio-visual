"""R4 operator legibility — persisted ``block_reason`` on pipeline_state (v0.7.0).

Adds the authoritative reason a version's pipeline is ``blocked`` (F-007 / R4, D1):

* ``pipeline_state.block_reason`` (VARCHAR(20), nullable) — one of agent_question / agent_error /
  system_error / parse_exhaustion, SET at each block site and CLEARED by the ORM status set-listener when
  the state leaves ``blocked``. Replaces the FE ``lastMessage.author == "system"`` heuristic.
* ``ck_pipeline_state_block_reason`` CHECK — NULL or one of the canonical values; the body mirrors the
  model constraint VERBATIM (``BLOCK_REASON_VALUES`` via ``_sql_in_list``) → zero schema drift.

Additive + nullable → no backfill (an in-flight blocked row simply falls back to the FE heuristic until its
next status write). Idempotent (``ADD COLUMN IF NOT EXISTS`` + guarded constraint create) so a re-run, or a
clean DB whose ``create_all`` already built the column + CHECK, never errors.

Revision ID: 067
Revises: 066
Create Date: 2026-06-17

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors backend/db/models/pipeline.py BLOCK_REASON_VALUES + the model CHECK body verbatim.
_BLOCK_REASON_IN = "'agent_question', 'agent_error', 'system_error', 'parse_exhaustion'"


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS block_reason VARCHAR(20)")
    # Drop-then-create so a re-run (or a clean DB whose create_all already built it) lands on one
    # constraint with the exact model body — no duplicate-name error, no drift.
    op.execute("ALTER TABLE pipeline_state DROP CONSTRAINT IF EXISTS ck_pipeline_state_block_reason")
    op.create_check_constraint(
        "ck_pipeline_state_block_reason",
        "pipeline_state",
        f"block_reason IS NULL OR block_reason IN ({_BLOCK_REASON_IN})",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_state DROP CONSTRAINT IF EXISTS ck_pipeline_state_block_reason")
    op.execute("ALTER TABLE pipeline_state DROP COLUMN IF EXISTS block_reason")
