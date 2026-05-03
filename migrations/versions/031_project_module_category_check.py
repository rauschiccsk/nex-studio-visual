"""Restrict project_modules.category to the ICC label set.

Surfaced in NEX Test Krok 1 audit: category was a free-text field,
which would let typos drift ("system" vs "Systém") and break the
multi-module sidebar grouping. Lock it to the seven SK-localized
labels used in NEX Automat's module_registry.yaml.

Existing rows were spot-checked before this migration (all
``Systém`` / ``Katalógy``), so no data sanitisation is needed — the
CHECK applies cleanly to live rows.

Revision ID: 031
Revises: 030
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CHECK_NAME = "ck_project_modules_category"
_CHECK_EXPR = "category IN ('Systém', 'Katalógy', 'Sklad', 'Predaj', 'Nákup', 'Účtovníctvo', 'Pokladňa')"


def upgrade() -> None:
    op.create_check_constraint(_CHECK_NAME, "project_modules", _CHECK_EXPR)


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "project_modules", type_="check")
