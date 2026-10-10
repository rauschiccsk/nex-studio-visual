"""One valid delivered-token statement per version — a new issue replaces the previous one (DEV-54).

Director 10.10.2026, after issuing NEX Inbox 1.7.0 twice (423,87 € and 249,98 €) with nothing saying which one is
valid: a new statement of the same version replaces the previous one; the old one stays in the list, marked as
replaced, so nothing is lost — but only one is valid, and an invoice is made from that one.

* ``replaces_id`` — the statement this one replaced; ``replaced_at`` — when this one was replaced in turn.
* ``ux_delivery_statements_one_valid`` — the database keeps one valid (not replaced) statement per version.

Statements issued before this revision are read as they happened: on each version every issue replaced the one
before it, so the newest stays valid and each older one is replaced at the moment the next one was issued.

Revision ID: 112
Revises: 111
Create Date: 2026-10-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "112"
down_revision: Union[str, None] = "111"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "delivery_statements",
        sa.Column(
            "replaces_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_statements.id", name="delivery_statements_replaces_id_fkey"),
            nullable=True,
        ),
    )
    op.add_column("delivery_statements", sa.Column("replaced_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   lag(id) OVER w AS previous_id,
                   lead(created_at) OVER w AS next_issued_at
            FROM delivery_statements
            WINDOW w AS (PARTITION BY version_id ORDER BY created_at, id)
        )
        UPDATE delivery_statements AS s
        SET replaces_id = ordered.previous_id, replaced_at = ordered.next_issued_at
        FROM ordered
        WHERE s.id = ordered.id AND (ordered.previous_id IS NOT NULL OR ordered.next_issued_at IS NOT NULL)
        """
    )
    op.create_check_constraint(
        "ck_delivery_statements_replaces_other", "delivery_statements", "replaces_id IS NULL OR replaces_id <> id"
    )
    op.create_index(
        "ux_delivery_statements_one_valid",
        "delivery_statements",
        ["version_id"],
        unique=True,
        postgresql_where=sa.text("replaced_at IS NULL"),
    )
    op.create_index(
        "ux_delivery_statements_replaces",
        "delivery_statements",
        ["replaces_id"],
        unique=True,
        postgresql_where=sa.text("replaces_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_delivery_statements_replaces")
    op.execute("DROP INDEX IF EXISTS ux_delivery_statements_one_valid")
    op.execute("ALTER TABLE delivery_statements DROP CONSTRAINT IF EXISTS ck_delivery_statements_replaces_other")
    op.execute("ALTER TABLE delivery_statements DROP COLUMN IF EXISTS replaced_at")
    op.execute("ALTER TABLE delivery_statements DROP COLUMN IF EXISTS replaces_id")
