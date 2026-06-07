"""Drop the orphaned ``projects.ui_design_port`` column (CR-NS-019).

The UI-design mockup feature was removed: its ``ui_designs`` table was dropped by
048 (CR-NS-008) and the mockup service + its dead per-project port allocation are
removed here. The ``ck_projects_ports_distinct`` CHECK references ``ui_design_port``,
so it is dropped + recreated (without the 3 ui_design pairs) around the column drop;
a bare DROP COLUMN would fail / leave a stale constraint. Per-project ports drop 4→3.

Revision ID: 056
Revises: 055
Create Date: 2026-06-07

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_projects_ports_distinct"

# 3 pairs (no ui_design) — matches the model after CR-NS-019.
_CHECK_3 = (
    "(backend_port IS NULL OR frontend_port IS NULL OR backend_port <> frontend_port) "
    "AND (backend_port IS NULL OR db_port IS NULL OR backend_port <> db_port) "
    "AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port)"
)
# 6 pairs (with ui_design) — the pre-CR-NS-019 constraint, restored on downgrade.
_CHECK_6 = (
    "(backend_port IS NULL OR frontend_port IS NULL OR backend_port <> frontend_port) "
    "AND (backend_port IS NULL OR db_port IS NULL OR backend_port <> db_port) "
    "AND (backend_port IS NULL OR ui_design_port IS NULL OR backend_port <> ui_design_port) "
    "AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port) "
    "AND (frontend_port IS NULL OR ui_design_port IS NULL OR frontend_port <> ui_design_port) "
    "AND (db_port IS NULL OR ui_design_port IS NULL OR db_port <> ui_design_port)"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.drop_column("projects", "ui_design_port")
    op.create_check_constraint(_CONSTRAINT, "projects", _CHECK_3)


def downgrade() -> None:
    import sqlalchemy as sa

    op.drop_constraint(_CONSTRAINT, "projects", type_="check")
    op.add_column("projects", sa.Column("ui_design_port", sa.Integer(), nullable=True))
    op.create_check_constraint(_CONSTRAINT, "projects", _CHECK_6)
