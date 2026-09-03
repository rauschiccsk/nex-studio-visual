"""Projekt si pamätá, že bol prevzatý — bez toho sa charta nedá obnovovať (ICCINT-51).

``projects.adopted`` (BOOLEAN NOT NULL DEFAULT false).

02.09.2026: do šablóny charty pribudlo nové pravidlo. Overenie ukázalo, že sa nedostalo do žiadneho
existujúceho projektu — charta sa píše RAZ, pri založení (``provision_v2_agent_charters``), a engine ju
pri každom spustení agenta číta z tej zamrznutej kópie. Šablóna má od 23.08.2026 jediného vlastníka,
ale kópie sa už neobnovujú, takže každé zlepšenie pravidiel sa mlčky zastaví pred existujúcimi stavbami.

Obnovovať chartu sa však nedá naslepo: prevzatý projekt (``--adopt``) si podľa CLAUDE.md §1 drží VLASTNÉ
pravidlá a do tých sa nesiaha. Hodnota ``adopted`` sa pritom dovtedy počítala pri zakladaní
(``adopted=not scaffolded_here``, ``api/routes/projects.py``), použila raz a zabudla — v modeli projektu
nebola. Engine teda tie dva prípady nemal ako rozlíšiť.

Dorovnanie: ``false`` pre všetky existujúce riadky je SPRÁVNE, nie pohodlné. V evidencii sú k 03.09.2026
tri projekty (nex-productcatalogs, nex-shopify, nex-websites) a pri všetkých troch je na disku overené,
že prevzaté nie sú — prevzatý projekt si NEX Studio odkladá pôvodné pravidlá ako ``CLAUDE.md.pre-nex-studio``
a ani jeden ten súbor nemá. (Majú ho nex-horizont a nex-marina, ktoré v evidencii nie sú.)

Revision ID: 094
Revises: 093
Create Date: 2026-09-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "094"
down_revision: Union[str, None] = "093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("projects", "adopted")
