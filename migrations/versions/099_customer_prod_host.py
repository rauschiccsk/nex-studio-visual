"""Ostrá prevádzka zákazníka môže bežať na inom stroji než kokpit (ICCINT-151).

**Čo sa dialo.** MÁGERSTAV má vlastný server. Jeho ostrá prevádzka — NEX Inbox aj NEX Manager —
tam beží, ale kokpit na ten stroj nedosiahol: nasadzoval výhradne tam, kde beží sám. Každé vydanie
pre MÁGERSTAV sa preto prenášalo rukami. Director 24.09.2026: *„Žiadne ručné nasadenia už nebudú.
Má to fungovať presne tak ako nasadzujem UAT, len nasadenie UAT-u robíme na ANDROS Serveri a PROD
ide na MAGER Server."*

**Prečo pri zákazníkovi a nie pri projekte.** Cieľ nie je vlastnosť aplikácie, ale zákazníka:
MAGER je server MÁGERSTAVu a beží na ňom jeho Inbox aj jeho Manager. Keby to viselo na projekte,
znamenalo by to „NEX Inbox sa vždy nasadzuje na MAGER" — a to neplatí, iný zákazník ho má inde.

**Prázdna hodnota znamená „tento stroj".** Testovacie inštalácie sa nepýtajú: tie zostávajú vždy
tam, kde beží kokpit.

Revision ID: 099
Revises: 098
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "099"
down_revision: Union[str, None] = "098"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("prod_host", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "prod_host")
