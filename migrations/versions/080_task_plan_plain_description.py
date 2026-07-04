"""Add the nullable ``plain_description`` column to ``epics`` / ``feats`` / ``tasks`` — the plain-language
one-liner of the STEP 3 Plán úloh three-layer rail (redesign Chrbtica; step3-plan-design.md, Task 1).

The redesign's Riadiace centrum shows the task plan as a three-layer map: L1 the node title + status, L2 a
plain-language (jargon-free) one-liner per node, L3 the technical detail on expand. ``plain_description`` is
that L2 prose — distinct from the technical ``description`` (which ``feats``/``tasks`` already carry and the
Epic deliberately does NOT — so for an Epic this is its ONLY prose). The AI partner emits it on the
generating schema (default empty so an omission parses); the FE renders a muted placeholder when it is empty
and NEVER falls back to the technical ``description``.

Net-new nullable ``TEXT`` on three tables — purely additive, NO backfill (NULL is the correct "no plain
one-liner yet" default for every existing row, so every existing plan is byte-identical), NO CHECK
constraint. Does NOT touch any constrained column.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the column) never errors — mirrors 079 verbatim.

Revision ID: 080
Revises: 079
Create Date: 2026-07-04

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "080"
down_revision: Union[str, None] = "079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE epics ADD COLUMN IF NOT EXISTS plain_description TEXT")
    op.execute("ALTER TABLE feats ADD COLUMN IF NOT EXISTS plain_description TEXT")
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS plain_description TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS plain_description")
    op.execute("ALTER TABLE feats DROP COLUMN IF EXISTS plain_description")
    op.execute("ALTER TABLE epics DROP COLUMN IF EXISTS plain_description")
