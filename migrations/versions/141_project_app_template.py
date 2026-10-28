"""A project remembers the app template it was founded on (DEV-253).

``projects.template`` — the id of the template (nex-app-kit, e.g. ``nsv-standard``) or NULL for a project without
one (every project founded before, and any founded „bez šablóny“). The agents' instructions, the Vizuál brief and the
gates read it: the frame of a project on a template comes from the template. Director 28.10.2026: „Áno, súhlasím,
zverejni ho a pokračuj treťou fázou“.

Revision ID: 141
Revises: 140
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "141"
down_revision: Union[str, None] = "140"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("template", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "template")
