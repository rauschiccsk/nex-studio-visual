"""How a person arranged the cockpit's screen — the widths of the side panels and the collapsed sidebar (DEV-82).

12.10.2026 the Director wanted to change the widths of the side panels, kept per user:
„Treba aby systém zapamätal tieto nastavenia pre každého užívateľa osobitne.“
One row per user, kept on the server so it is the same on every computer; an empty width is the default. The bounds
(sidebar 180–360 px, the task plan 280–640 px) are checked here too.

Revision ID: 120
Revises: 119
Create Date: 2026-10-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "120"
down_revision: Union[str, None] = "119"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_layouts",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sidebar_width", sa.Integer(), nullable=True),
        sa.Column("sidebar_collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan_width", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("sidebar_width BETWEEN 180 AND 360", name="ck_user_layouts_sidebar_width"),
        sa.CheckConstraint("plan_width BETWEEN 280 AND 640", name="ck_user_layouts_plan_width"),
    )


def downgrade() -> None:
    op.drop_table("user_layouts")
