"""Every line of a delivered-token statement carries its own amount (DEV-50).

Director 10.10.2026: „Ešte by som chcel doplniť pre každý riadok na konci sumu“. Each line — code and tests,
documentation — is its tokens / 1 000 × its rate, rounded to cents, and the total is their sum, so an invoice built
from the lines never differs from the statement by a cent. The line amounts are frozen with the statement, as its
rates are.

A statement issued before this revision gets its line amounts from its own frozen tokens and rates, the same way —
but only where they add up to the total it was issued with (its total is never rewritten); otherwise they stay
NULL and the statement shows its total alone. The check constraint keeps every later row adding up.

Revision ID: 111
Revises: 110
Create Date: 2026-10-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "111"
down_revision: Union[str, None] = "110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("delivery_statements", sa.Column("amount_code_eur", sa.Numeric(12, 2), nullable=True))
    op.add_column("delivery_statements", sa.Column("amount_docs_eur", sa.Numeric(12, 2), nullable=True))
    # PostgreSQL's round(numeric) rounds half away from zero — for amounts (never negative) that is ROUND_HALF_UP,
    # the rounding the cockpit issues with.
    op.execute(
        """
        WITH lines AS (
            SELECT id,
                   CASE WHEN work_kind = 'fix' THEN 0.00
                        ELSE round((tokens_code + tokens_tests)::numeric / 1000 * rate_code, 2) END AS code,
                   CASE WHEN work_kind = 'fix' THEN 0.00
                        ELSE round(tokens_docs::numeric / 1000 * rate_docs, 2) END AS docs
            FROM delivery_statements
        )
        UPDATE delivery_statements AS s
        SET amount_code_eur = lines.code, amount_docs_eur = lines.docs
        FROM lines
        WHERE s.id = lines.id AND lines.code + lines.docs = s.amount_eur
        """
    )
    op.create_check_constraint(
        "ck_delivery_statements_lines_add_up",
        "delivery_statements",
        "(amount_code_eur IS NULL AND amount_docs_eur IS NULL) OR "
        "(amount_code_eur >= 0 AND amount_docs_eur >= 0 AND amount_code_eur + amount_docs_eur = amount_eur)",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE delivery_statements DROP CONSTRAINT IF EXISTS ck_delivery_statements_lines_add_up")
    op.execute("ALTER TABLE delivery_statements DROP COLUMN IF EXISTS amount_docs_eur")
    op.execute("ALTER TABLE delivery_statements DROP COLUMN IF EXISTS amount_code_eur")
