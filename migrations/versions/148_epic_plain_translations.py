"""An epic's plain description in the app's other languages (DEV-418).

**What happened.** 17.11.2026: an app on nex-app-kit 0.16.0 says Aktualizácie in the person's language from
``RELEASE_NOTES.<language>.md``, but the notes the cockpit writes itself (from the epics' plain descriptions) were
Slovak only. Director, on the method — the plan writer writes each epic's plain description in every language of the
app and the cockpit assembles the note in each of them: „Súhlasím“.

**What it changes.** Adds ``epics.plain_translations`` — ``{"hu": "…", "en": "…"}``, the epic's plain description in
the app's languages other than Slovak (the language of ``plain_description``). ``NULL`` for every existing epic: its
version's notes stay in the main language, which the template says for every other language.

**Downgrade** drops the column.

Revision ID: 148
Revises: 147
Create Date: 2026-11-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "148"
down_revision: Union[str, None] = "147"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("epics", sa.Column("plain_translations", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("epics", "plain_translations")
