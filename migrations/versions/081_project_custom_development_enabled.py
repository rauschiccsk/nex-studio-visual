"""Add the ``projects.custom_development_enabled`` flag — "Vývoj na zákazku" (STEP 6, step6-hotovo-design.md R9).

The redesign's Hotovo step ships a create-time-only project flag: ``custom_development_enabled`` is the ONLY
switch that later permits a project to deviate from the unified default company design (firemné zásady §4). It
is set ONCE at project creation (like ``type`` / ``auth_mode`` — excluded from ``ProjectUpdate``) and is an
INERT stored datum in STEP 6 — no behaviour binds to it yet (the deviation gate is a future scope). Clones the
``guardian_enabled`` precedent (a ``NOT NULL DEFAULT false`` boolean).

Net-new ``BOOLEAN NOT NULL DEFAULT false`` on ``projects`` — purely additive, the server default backfills
every existing row to ``false`` (no custom-development project exists yet), so every existing project is
byte-identical. Does NOT touch any constrained column.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS`` so a re-run (or a clean DB whose
``create_all`` already built the column) never errors — mirrors 079 / 080 verbatim.

Revision ID: 081
Revises: 080
Create Date: 2026-07-05

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS custom_development_enabled BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS custom_development_enabled")
