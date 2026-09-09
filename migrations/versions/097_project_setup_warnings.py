"""Čo sa pri zakladaní projektu nedorobilo, sa zapíše — nezanikne v odpovedi (ICCINT-88).

**Čo sa dialo.** Pri prevzatí projektu kokpit zostavil vetu o tom, čo sa zámerne vynechalo (CI,
ochrana vetvy, skúšobné spustenie) — a nikto ju neuvidel. Žila len v odpovedi na založenie, a dialóg
prevzatia po úspechu odchádza na stránku projektu, takže odpoveď zanikla skôr, než sa dala prečítať.
Zmerané 09.09.2026 pri prevzatí NEX Managera.

**Prečo na tom záleží.** Správa, ktorú nikto neprečíta, je to isté ako ticho — a práve tomu má
brániť. Manažér nemá ako vedieť, že prevzatý projekt si CI a ochranu vetvy drží vlastnú.

**Čo robí tento stĺpec.** Drží ten istý zoznam viet pri projekte, takže ho stránka projektu môže
ukázať kedykoľvek. Nie je to upozornenie na odkliknutie, ale ZÁZNAM o tom, ako projekt vznikol —
platí, kým to niekto ručne nedorobí.

Revision ID: 097
Revises: 096
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "097"
down_revision: Union[str, None] = "096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("setup_warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("projects", "setup_warnings")
