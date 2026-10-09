"""Widen the block-reason CHECK for a database change that waits for Ri (DEV-7).

``pipeline_state.block_reason`` += ``'schema_approval'`` — the AI Agent needs a database change in Programovanie
and ``icc/SCHEMA_GOVERNANCE.md`` gives its approval to Ri alone. It needs its own value because the screen offers
a different action for it (approve with one click, Ri only) than for an ordinary question (a free-text answer).

02.10.2026, dedo-home: the schema was approved in a free-text answer and copied into the Knowledge Base by Dedo
from a terminal. The cockpit now publishes it itself once Ri approves.

A CHECK-constraint value widening on an existing String column — drop + re-add with the widened list, as
``092_block_reason_check_failed``. The downgrade narrows the list back and first rewrites any ``schema_approval``
row to ``agent_question`` (the free-text question it would have been), so it cannot fail against live data.

Revision ID: 108
Revises: 107
Create Date: 2026-10-09

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "108"
down_revision: Union[str, None] = "107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASON_OLD = "agent_question,decision_needed,agent_error,system_error,parse_exhaustion,framework_issue,check_failed"
_REASON_NEW = f"{_REASON_OLD},schema_approval"


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
    op.execute("UPDATE pipeline_state SET block_reason = 'agent_question' WHERE block_reason = 'schema_approval'")
    _set_reason(_REASON_OLD)
