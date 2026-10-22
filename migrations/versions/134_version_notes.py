"""„Kde sme skončili“ — notes at a version, why its development stands and what next (DEV-151).

Director 20.10.2026: „Aby v prípade, že vývoj projektu z nejakej príčiny nedokončíme … mali by sme presnú informáciu
o tom, kde a ako pokračovať v projekte.“ To the plan: „Súhlasím“.

Revision ID: 134
Revises: 133
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "134"
down_revision: Union[str, None] = "133"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "version_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
        ),
    )
    op.create_index("ix_version_notes_version_id", "version_notes", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_version_notes_version_id", table_name="version_notes")
    op.drop_table("version_notes")
