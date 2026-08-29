"""Widen the block-reason CHECK for a failed engine CHECK (ICCINT-43).

``pipeline_state.block_reason`` += ``'check_failed'`` — a check the engine ran itself came back negative.
Nobody failed: not the agent, not the engine. It needs its own value because the cockpit renders the reason
as a sentence to the Manažér, and every other reason's sentence names a culprit.

29.08.2026, nex-productcatalogs: the end-of-Programovanie boot re-check (ICCINT-42) borrowed ``agent_error``
and the screen read "Niečo zlyhalo — Agent zlyhal" over an agent whose fix had in fact worked. The Director
reported it as an agent failure, because that is what he was told.

A CHECK-constraint value widening on an existing String column (the codebase's String+CHECK convention) —
drop + re-add with the widened list. No data migration, no new column. Idempotent: DROP CONSTRAINT IF EXISTS.
The downgrade narrows the list back and first rewrites any ``check_failed`` row to ``agent_error``, so it
cannot fail against live data.

Revision ID: 092
Revises: 091
Create Date: 2026-08-29

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "092"
down_revision: Union[str, None] = "091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASON_OLD = "agent_question,decision_needed,agent_error,system_error,parse_exhaustion,framework_issue"
_REASON_NEW = f"{_REASON_OLD},check_failed"


def _in_list(csv: str) -> str:
    return ", ".join(f"'{v}'" for v in csv.split(","))


def _set_reason(values_csv: str) -> None:
    op.execute("ALTER TABLE pipeline_state DROP CONSTRAINT IF EXISTS ck_pipeline_state_block_reason")
    op.execute(
        f"ALTER TABLE pipeline_state ADD CONSTRAINT ck_pipeline_state_block_reason "
        f"CHECK (block_reason IS NULL OR block_reason IN ({_in_list(values_csv)}))"
    )


def upgrade() -> None:
    _set_reason(_REASON_NEW)


def downgrade() -> None:
    op.execute("UPDATE pipeline_state SET block_reason = 'agent_error' WHERE block_reason = 'check_failed'")
    _set_reason(_REASON_OLD)
