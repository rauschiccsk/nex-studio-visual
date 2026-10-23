"""The human-developer minutes of the task plan are gone — the cockpit forecasts the agent's work itself (DEV-210).

``feats.estimated_minutes`` and ``tasks.estimated_minutes`` held what the agent was told to write as „a realistic
estimate for a capable HUMAN developer“; the plan summed them into „odhad ~3260 min (~54.3 h)“ for NEX Manager 1.4.0,
whose tasks took the agent about an hour. They were asked for when work was to be billed by the hour; billing went to
tokens, and the forecast now comes from the agent's measured work (``build_forecast``). Director 23.10.2026: „Ludské
hodnoty treba zrušiť … Ja potrebujem odhad, že agent približne koľko to bude robiť.“

Revision ID: 140
Revises: 139
Create Date: 2026-10-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "140"
down_revision: Union[str, None] = "139"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("tasks", "estimated_minutes")
    op.drop_column("feats", "estimated_minutes")


def downgrade() -> None:
    op.add_column("feats", sa.Column("estimated_minutes", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("estimated_minutes", sa.Integer(), nullable=True))
