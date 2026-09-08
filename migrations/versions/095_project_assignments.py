"""Projekt sa dá zveriť inému pracovníkovi a je vidieť, komu patril predtým (ICCINT-78).

Vlastníctvo zostáva tam, kde vždy bolo — ``projects.created_by`` (viď ``backend/core/authz.py``: pravidlo
je *vlastník alebo admin*, žiadne úrovne medzi tým). Nepribúda tretí stĺpec; pribúda možnosť ten jediný
ZMENIŤ, a záznam o tom, kto komu čo zveril.

**Prečo história a nie len prepísané pole.** Dve veci naraz:

1. Presun neslúži len na trvalé odovzdanie, ale aj na **zastupovanie počas neprítomnosti** (Director
   08.09.2026: Nazar odcestuje, projekt naňho počká u Tibora). Bez záznamu sa po pol roku nedá povedať,
   či bol presun natrvalo, alebo na týždeň.
2. Keď sa ``created_by`` začne prepisovať, prestane znamenať „kto to založil“. Prvý riadok histórie si
   pôvodného zakladateľa pamätá, takže sa tá informácia nestratí.

**Prečo to vôbec vzniká.** Director s Tiborom si dovtedy vymieňali prihlasovacie údaje kvôli
zastupiteľnosti. 08.09.2026 to zrušil, lebo zdieľané heslo zmaže stopu, kto čo urobil — a pravidlo
„nič nezvratné bez Directora“ sa opiera práve o podpis konta. Presun projektu je náhrada, ktorá stopu
zachová. Bez neho niet čím tú zastupiteľnosť nahradiť (D-028).

``assigned_by`` je zámerne NOT NULL: presun smie jedine admin a musí byť vidieť ktorý.

Revision ID: 095
Revises: 094
Create Date: 2026-09-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "095"
down_revision: Union[str, None] = "094"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_assignments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        # NULL len pri prvom riadku (založenie) — dovtedy projekt nikomu „nepatril od niekoho".
        sa.Column("from_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("to_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assigned_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_project_assignments_project", "project_assignments", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_assignments_project", table_name="project_assignments")
    op.drop_table("project_assignments")
