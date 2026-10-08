"""Poradcova požiadavka ide len do Zásobníka — verzia z nej nevzniká (DEV-29, v4.43.7).

**Čo sa dialo.** Tlačidlo „Založiť novú verziu z tejto požiadavky" pod odpoveďou Poradcu založilo požiadavku
v Zásobníku AJ ďalšiu verziu projektu so zadaním z nej. Director 08.10.2026: „O verziách rozhodujem ja.
Treba, aby zapísal len do zásobníku." Odpoveď si pamätala založenú verziu (``captured_version_id``).

**Čo mení.** Odpoveď si odteraz pamätá požiadavku v Zásobníku, ktorú z nej človek uložil
(``captured_backlog_item_id``, pri zmazaní požiadavky SET NULL) — druhé kliknutie vráti tú istú.
``captured_version_id`` sa ruší: na PROD ho nemala ani jedna odpoveď (zmerané 08.10.2026: 0).

**Späť** sa vráti prázdny stĺpec ``captured_version_id`` a stĺpec požiadavky zmizne.

Revision ID: 106
Revises: 105
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "106"
down_revision: Union[str, None] = "105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "poradca_messages",
        sa.Column(
            "captured_backlog_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backlog_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.drop_column("poradca_messages", "captured_version_id")


def downgrade() -> None:
    op.add_column(
        "poradca_messages",
        sa.Column(
            "captured_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.drop_column("poradca_messages", "captured_backlog_item_id")
