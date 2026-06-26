"""Operator rename Director → Manažér for v2.0.0 (CR-V2-004): status VALUE + participant token.

The human operator label is renamed across the pipeline enums:

* ``pipeline_state.status``  ``'awaiting_director'`` → ``'awaiting_manazer'`` (``ck_pipeline_state_status``)
* ``pipeline_message.author`` / ``recipient`` participant token ``'director'`` → ``'manazer'``
  (``ck_pipeline_message_author`` / ``ck_pipeline_message_recipient`` — replacing the ``'director'``
  that migration 069 carried over from v1)

This mirrors ``backend/db/models/pipeline.py`` (``STATUS_VALUES`` / ``PARTICIPANT_VALUES``), so the DB
CHECKs and the Pydantic ``Literal`` schemas stay in lock-step (the enum-tuple-single-source pattern).

**Only the STATUS *value* rename is DDL** (the CHECK constraints). The columns ``awaiting_director_since``
and ``total_director_wait_seconds`` KEEP their names — renaming live columns would be needless churn
(CR-V2-004 scope: operator/status relabel, not a column rename). ``status`` stays ``String(20)``
(``'awaiting_manazer'`` is 16 chars, fits; no resize).

Data: existing rows carrying the old value are DATA-MIGRATED to the new value BEFORE the new CHECKs are
re-created, so the constraint validation never fails on live data (a no-op on a fresh/CI DB).

Idempotent: drops use ``IF EXISTS`` (mirrors migration 064/069) so a re-run — or a fresh DB whose
``create_all`` already built the v2 CHECKs — never errors. ``downgrade`` data-migrates back and restores
the pre-rename CHECKs.

Revision ID: 071
Revises: 070
Create Date: 2026-06-26

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# --- post-rename value sets (mirror backend/db/models/pipeline.py STATUS_VALUES / PARTICIPANT_VALUES) ---
_STATUS_NEW = "'agent_working', 'awaiting_manazer', 'blocked', 'paused', 'done'"
_PARTICIPANT_NEW = "'ai_agent', 'auditor', 'manazer', 'system'"

# --- pre-rename value sets (restored on downgrade — the v1 'director' / 'awaiting_director' labels) ---
_STATUS_OLD = "'agent_working', 'awaiting_director', 'blocked', 'paused', 'done'"
_PARTICIPANT_OLD = "'ai_agent', 'auditor', 'director', 'system'"


def _rewrite(table: str, name: str, column: str, values: str) -> None:
    """Drop (IF EXISTS, for idempotency) and recreate a CHECK constraint with a new ``IN`` list."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.create_check_constraint(name, table, f"{column} IN ({values})")


def upgrade() -> None:
    # Data-migrate existing rows to the new operator label BEFORE re-creating the CHECKs (no-op on a
    # fresh DB; renames any live v1 rows on the dev branch DB). Order matters: the value must be the new
    # one before the new CHECK is validated.
    op.execute("UPDATE pipeline_state SET status = 'awaiting_manazer' WHERE status = 'awaiting_director'")
    op.execute("UPDATE pipeline_message SET author = 'manazer' WHERE author = 'director'")
    op.execute("UPDATE pipeline_message SET recipient = 'manazer' WHERE recipient = 'director'")

    _rewrite("pipeline_state", "ck_pipeline_state_status", "status", _STATUS_NEW)
    _rewrite("pipeline_message", "ck_pipeline_message_author", "author", _PARTICIPANT_NEW)
    _rewrite("pipeline_message", "ck_pipeline_message_recipient", "recipient", _PARTICIPANT_NEW)


def downgrade() -> None:
    # Restore the v1 labels: data-migrate back, then re-create the pre-rename CHECKs.
    op.execute("UPDATE pipeline_state SET status = 'awaiting_director' WHERE status = 'awaiting_manazer'")
    op.execute("UPDATE pipeline_message SET author = 'director' WHERE author = 'manazer'")
    op.execute("UPDATE pipeline_message SET recipient = 'director' WHERE recipient = 'manazer'")

    _rewrite("pipeline_state", "ck_pipeline_state_status", "status", _STATUS_OLD)
    _rewrite("pipeline_message", "ck_pipeline_message_author", "author", _PARTICIPANT_OLD)
    _rewrite("pipeline_message", "ck_pipeline_message_recipient", "recipient", _PARTICIPANT_OLD)
