"""Zmazanie používateľa už nezoberie jeho rozhovory s Poradcom ani ich cenu (ICCINT-169, v4.43.2).

**Čo sa dialo.** Cudzí kľúč ``poradca_conversations.author_id`` mal ON DELETE CASCADE: zmazanie používateľa
(kôš pri používateľovi v kokpite) zmazalo jeho rozhovory aj s odpoveďami — a cenu tých odpovedí z Nákladov
projektu, hoci sa naozaj minula. Projekty a chyby už zmazanie používateľa blokujú (RESTRICT); rozhovory chýbali.

**Čo mení.** Kľúč na ON DELETE RESTRICT; kokpit pred zmazaním povie, prečo nejde, a ponúkne deaktiváciu
(``user_service._has_restrict_dependencies``).

**Späť** sa kľúč vráti na CASCADE.

Revision ID: 105
Revises: 104
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "105"
down_revision: Union[str, None] = "104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "poradca_conversations_author_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "poradca_conversations", type_="foreignkey")
    op.create_foreign_key(_FK, "poradca_conversations", "users", ["author_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint(_FK, "poradca_conversations", type_="foreignkey")
    op.create_foreign_key(_FK, "poradca_conversations", "users", ["author_id"], ["id"], ondelete="CASCADE")
