"""A whole version or fast fix may be a gift — the third work kind (DEV-118).

Director 14.10.2026: „BUG - oprava, GIFT - Dar, a požiadavka zákazníka.“ A task could already be a gift (DEV-75);
now the work kind of a version and of its issued statement may be ``gift`` too.

Revision ID: 124
Revises: 123
Create Date: 2026-10-14

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "124"
down_revision: Union[str, None] = "123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_versions_work_kind", "versions", type_="check")
    op.create_check_constraint(
        "ck_versions_work_kind", "versions", "work_kind IS NULL OR work_kind IN ('fix', 'change', 'gift')"
    )
    op.drop_constraint("ck_delivery_statements_work_kind", "delivery_statements", type_="check")
    op.create_check_constraint(
        "ck_delivery_statements_work_kind", "delivery_statements", "work_kind IN ('fix', 'change', 'gift')"
    )


def downgrade() -> None:
    # A gift has no older kind to fall back to: while one exists, the old constraint cannot be created and the
    # downgrade fails — it never rewrites what was billed.
    op.drop_constraint("ck_delivery_statements_work_kind", "delivery_statements", type_="check")
    op.create_check_constraint(
        "ck_delivery_statements_work_kind", "delivery_statements", "work_kind IN ('fix', 'change')"
    )
    op.drop_constraint("ck_versions_work_kind", "versions", type_="check")
    op.create_check_constraint(
        "ck_versions_work_kind", "versions", "work_kind IS NULL OR work_kind IN ('fix', 'change')"
    )
