"""Billing category per task, discount per category, statement lines and the cached line authorship (DEV-75).

12.10.2026 the Director: besides bugs and customer requests he needs a third kind — a gift, an attention of ICC. It goes
on the invoice and may carry a 100 % discount; the category is set for every task. Approved design:

* ``tasks.billing_category`` — ``bug`` / ``request`` / ``gift``; empty = the version's own (a fast fix of our error →
  bug, otherwise request);
* ``versions.discount_*`` — the discount of each category for this version, in %; empty = the default in Nastavenia;
  every change kept in ``version_discount_changes`` (as the rates of DEV-74);
* ``delivery_statements.lines`` — an issued statement freezes its lines (category × code/docs, tokens, rate, full
  value, discount, amount); a statement issued before keeps none and is read as before;
* ``delivered_authorship`` — which commit wrote each delivered line (``git blame`` of the delivered state, seconds
  per version), kept once per (base, delivered) pair: commits never change, so the answer never does.

Revision ID: 119
Revises: 118
Create Date: 2026-10-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "119"
down_revision: Union[str, None] = "118"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DISCOUNTS = ("discount_bug", "discount_request", "discount_gift")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("billing_category", sa.String(10), nullable=True))
    op.create_check_constraint("ck_tasks_billing_category", "tasks", "billing_category IN ('bug', 'request', 'gift')")
    for name in _DISCOUNTS:
        op.add_column("versions", sa.Column(name, sa.Numeric(5, 2), nullable=True))
    op.create_check_constraint(
        "ck_versions_discounts_range",
        "versions",
        " AND ".join(f"({n} >= 0 AND {n} <= 100)" for n in _DISCOUNTS),
    )
    op.create_table(
        "version_discount_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="CASCADE"), nullable=False),
        *[sa.Column(f"previous_{n}", sa.Numeric(5, 2), nullable=True) for n in _DISCOUNTS],
        *[sa.Column(n, sa.Numeric(5, 2), nullable=True) for n in _DISCOUNTS],
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
    )
    op.create_index("ix_version_discount_changes_version_id", "version_discount_changes", ["version_id"])
    op.add_column("delivery_statements", sa.Column("lines", JSONB, nullable=True))
    op.create_table(
        "delivered_authorship",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "base_sha", "head_sha", name="uq_delivered_authorship_range"),
    )


def downgrade() -> None:
    op.drop_table("delivered_authorship")
    op.drop_column("delivery_statements", "lines")
    op.drop_index("ix_version_discount_changes_version_id", table_name="version_discount_changes")
    op.drop_table("version_discount_changes")
    op.drop_constraint("ck_versions_discounts_range", "versions", type_="check")
    for name in _DISCOUNTS:
        op.drop_column("versions", name)
    op.drop_constraint("ck_tasks_billing_category", "tasks", type_="check")
    op.drop_column("tasks", "billing_category")
