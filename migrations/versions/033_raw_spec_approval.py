"""Add approval columns to raw_specifications.

Surfaced in NEX Test Krok 1 audit (2026-04-24): the Zákaznícka
špecifikácia had no explicit approval step — it transitioned from
``pending`` to ``done`` automatically once the AI finished generating
the Vývojová dokumentácia, which collapses authoring and approval
into a single invisible event.

This migration mirrors the ``approved_at`` / ``approved_by`` pair
already present on ``professional_specifications`` and
``ui_designs``, so the pipeline gets a proper schváľ action on every
artefact of the Krok 1 → Krok 2 → Krok 3 chain.

Both columns are nullable; existing rows are considered unapproved
and the router makes approval an explicit user gesture.

Revision ID: 033
Revises: 032
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_specifications",
        sa.Column(
            "approved_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "raw_specifications",
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("raw_specifications", "approved_at")
    op.drop_column("raw_specifications", "approved_by")
