"""Widen the block-reason CHECK for the agent → Dedo escalation (Director observation #6).

When the AI Agent hits an error it CANNOT fix because the fix needs a change to NEX Studio ITSELF (the
framework/tooling, §15 "fix NEX Studio, not the project"), it now escalates to Dedo instead of pushing the
unfixable failure onto the Manažér. The build settles ``blocked`` with a NEW ``block_reason`` value:

  * ``pipeline_state.block_reason`` += ``'framework_issue'`` — distinct from ``agent_error`` (a project-code
    failure the AI Agent CAN fix) so the cockpit shows "NEX Studio potrebuje opravu (Dedo) — počkaj" with NO
    recovery actions (``determine_available_actions`` returns EMPTY for it). Only Dedo clears it.

A CHECK-constraint value widening on the existing ``String`` column (the codebase's String+CHECK convention)
— drop + re-add the CHECK with the widened list. No data migration, no new column (the Dedo message lives in
the settle-path ``system→manazer`` notification's JSONB ``payload`` + the .dedo-channel inbox file).
Idempotent: DROP CONSTRAINT IF EXISTS. Mirrors migration 078 (consultation ``decision_needed``).

Revision ID: 082
Revises: 081
Create Date: 2026-07-07

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASON_NEW = "agent_question,decision_needed,agent_error,system_error,parse_exhaustion,framework_issue"
_REASON_OLD = "agent_question,decision_needed,agent_error,system_error,parse_exhaustion"


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
    _set_reason(_REASON_OLD)
