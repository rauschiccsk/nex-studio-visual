"""Kým sa na hranici fázy pracuje, kokpit to povie — a povie aj odkedy (ICCINT-75).

**Čo sa dialo.** Po kliknutí na „Schváliť vizuál“ bežala minúty práca — dohodnuté zmeny sa skladali späť
do dokumentov a Auditor prezeral, čo pribudlo. Celé to bežalo **vnútri toho kliknutia**, takže sa nič
z toho nezapísalo, kým to neskončilo: v evidencii stále stálo ``vizual/awaiting_manazer`` a obrazovka
čítala „čaká na súhlas“. Manažér videl ticho a povedal „nič sa nedeje, nefunguje to“ — pritom agent celý
ten čas pracoval (overené na procesoch v kontajneri, 07.09.2026, nex-productcatalogs v0.2.0).

**Prečo je ticho horšie než chyba.** Ticho pri práci a ticho pri poruche vyzerajú rovnako. Manažér nemá
ako rozhodnúť, či počkať, kliknúť znova, alebo volať pomoc — a klikanie znova spúšťa ďalšie ťahy, čo
stojí beh agenta a mätie protokol.

**Čo robí tento stĺpec.** Kliknutie odteraz iba **zapíše, že sa schválenie spracúva**, a hneď sa vráti;
samotnú prácu urobí ten istý mechanizmus na pozadí, ktorý vykonáva všetky ostatné ťahy. Stav sa tak
okamžite prepne na „pracuje sa“ a obrazovka to ukáže. Príznak sa spotrebuje a zmaže hneď na začiatku
ťahu — presne ako ``retry_consultation`` (ICCINT-25), aby nemohol prežiť do ďalšieho kola.

Revision ID: 096
Revises: 095
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "096"
down_revision: Union[str, None] = "095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pipeline_state",
        sa.Column("pending_vizual_signoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # „Pracuje sa“ bez toho, odkedy, je polovičná odpoveď: Manažér potrebuje rozoznať ťah spustený pred
    # pol minútou od ťahu, ktorý visí tretiu hodinu. Platí pre KAŽDÚ fázu, nielen pre schválenie Vizuálu.
    op.add_column("pipeline_state", sa.Column("working_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_state", "working_since")
    op.drop_column("pipeline_state", "pending_vizual_signoff")
