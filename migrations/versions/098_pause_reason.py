"""Pozastavená stavba povie, PREČO stojí — výzva a prekážka sa prestali tváriť rovnako (ICCINT-126).

**Čo sa dialo.** Director 14.09.2026 na NEX Inbox v1.5.0, fáza Verifikácia, vlastnými slovami:
*„Spravil som všetko podľa tvojho pokynu a nezbadal som, že tlačidlo nad plánom úloh zmenilo text."*
Stavba stála a nikto nevedel prečo.

Na rozhodovacej karte zvolil „Usmerniť opravu" a napísal pokyn. Pokyn sa zapísal správne. Stavba sa
potom prepla do stavu ``paused`` a čakala na druhé kliknutie, o ktorom Manažér nevedel — jediný
skutočne viditeľný signál bola zmena textu na tlačidle, teda presne to, čo oko prehliadne, lebo
tlačidlo tam bolo aj predtým.

**Prečo to nie je jedna vec.** ``paused`` dnes znamená tri rôzne veci naraz:

  * oprava podľa pokynu Manažéra je pripravená a čaká na jeho potvrdenie — to je VÝZVA,
  * stavba prekročila strop spracovania — to je PREKÁŽKA,
  * Manažér ju sám pozastavil — to VIE.

Prvé si žiada rovnako nápadný pruh ako ``decision_needed``; druhé a tretie nie. Bez tohto stĺpca sa
tie tri od seba nedajú odlíšiť inak než hádaním z textu ``next_action`` — a text sa preformuluje.

**Čo NEROBÍ.** Nezrušená ostáva samotná brána: druhé potvrdenie pri usmernenej oprave má zmysel,
Manažér tam posiela vlastný pokyn a má dostať možnosť si ho ešte pozrieť. Chyba nie je v tom, že sa
čaká — chyba je, že sa to nedá zbadať.

Revision ID: 098
Revises: 097
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "098"
down_revision: Union[str, None] = "097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_state", sa.Column("pause_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_state", "pause_reason")
