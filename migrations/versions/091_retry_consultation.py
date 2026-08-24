"""Add ``pipeline_state.retry_consultation`` — the way back after a consultation that could not be built (ICCINT-25).

When the independent review finds holes, the engine asks the AI Agent to turn them into Decision Cards. If
that ONE turn fails to come back — the model is briefly unreachable, the answer arrives unparseable — the
engine falls open to a plain ``awaiting_manazer`` stop with the findings listed as text. That fail-open is
right: nothing is lost and no build wedges.

What was missing is the way back. Found by the Director 24.08.2026 on nex-productcatalogs: an Anthropic
outage took both attempts, and when it passed there was no way to ask for the cards again — ``navrh`` /
``awaiting_manazer`` offers ``ask`` / ``schvalit`` / ``uprav`` and nothing else. A transient failure had
permanently downgraded HOW the Manažér decides, from one question at a time with options and a
recommendation to a wall of eleven findings and two buttons. Same shape as ICCINT-9 (one failed refresh and
the session never refreshed again), one layer up.

This column is the trigger, mirroring ``resume_after_framework_fix`` (migration 089) — ``determine_available_actions``
is state-only BY DESIGN, so the offer needs a column rather than a derived query. The action sets it while
flipping the build to ``agent_working``; the dispatch consumes and clears it before doing anything else, so
it can never survive a turn.

NOT NULL with a ``false`` server default: no build is mid-retry at migration time, so no backfill is needed.

Revision ID: 091
Revises: 090
Create Date: 2026-08-24

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "091"
down_revision: Union[str, None] = "090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent ADD COLUMN IF NOT EXISTS — mirrors 089 / 066, the sibling booleans on this table.
    op.execute("ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS retry_consultation BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_state DROP COLUMN IF EXISTS retry_consultation")
