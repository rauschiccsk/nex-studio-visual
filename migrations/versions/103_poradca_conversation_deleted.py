"""Vymazaný rozhovor Poradcu — text preč, cena ostáva (ICCINT-167, v4.42.0).

**Čo sa dialo.** Rozhovor Poradcu sa nedal premenovať ani odstrániť; zoznam vľavo len rástol. Director
05.10.2026: „chýba mi premenovanie rozhovoru a vymazanie rozhovoru."

**Prečo nie zmazanie riadkov.** Náklady projektu sčítajú spotrebu odpovedí Poradcu z ``poradca_messages``
(``metrics._poradca_rows``). Zmazané riadky by z Nákladov zobrali peniaze, ktoré sa naozaj minuli. Preto
vymazanie zahodí všetko, čo sa v rozhovore povedalo (otázky, odpovede, kroky, chyby, názov, záznam sedenia
na disku), ponechá len spotrebu a čas a rozhovor označí ``deleted_at`` — zo zoznamu aj z rozhrania zmizne.

**Späť** sa stĺpec zahodí; vymazané rozhovory by sa potom v zozname ukázali prázdne s názvom
„Vymazaný rozhovor" — nič z ich obsahu sa nevráti, lebo nič z neho neostalo.

Revision ID: 103
Revises: 102
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "103"
down_revision: Union[str, None] = "102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("poradca_conversations", "deleted_at")
