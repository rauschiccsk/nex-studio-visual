"""Verzia dostáva stav ``done`` — dokončená stavba prestáva vyzerať ako nezačatá (ICCINT-50).

``versions.status`` += ``'done'``.

01.09.2026, nex-productcatalogs v0.1.0: stavba prešla celým priebehom — Príprava, Návrh, Vizuál,
Programovanie, Verifikácia PASS, Manažér schválil na Hotovo. Priebeh je ``done/done``, plán 147/147.
Verzia pritom v evidencii zostala ``planned`` a na obrazovke sa zobrazila ako „Plánované".

Hotová a nezačatá verzia teda vyzerali rovnako — a keďže ``VersionDetailPage`` vetví obsah podľa stavu,
nad postavenou a schválenou verziou sa Manažérovi ponúkal panel „Zadanie" s návodom, ako ju spustiť.

Príčina: medzi ``planned`` a ``released`` neexistoval prechod, ktorý by zaznamenal, že sa stavba stala.
``auto_activate`` sa volalo jedine pri ručnej úprave epiky, čo priebeh nikdy nerobí.

Prečo štvrtý stav a nie ponechanie ``active`` až do nasadenia: dokončená verzia by potom vyzerala rovnako
ako tá, ktorá sa práve stavia — tá istá chyba prehodená inam. Životný cyklus má štyri stavy
(naplánovaná → stavia sa → hotová → vydaná), tak ich má mať aj evidencia. Rozhodol Director 03.09.2026.

Rozšírenie zoznamu v CHECK na existujúcom String stĺpci (konvencia String+CHECK) — drop + re-add.
Bez migrácie dát, bez nového stĺpca. Idempotentné: DROP CONSTRAINT IF EXISTS.
Downgrade zoznam zúži a najprv prepíše prípadné ``done`` riadky na ``active``, aby nepadol na živých dátach.

Revision ID: 093
Revises: 092
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op

revision: str = "093"
down_revision: Union[str, None] = "092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "ck_versions_status"
_OLD = "status IN ('planned', 'active', 'released')"
_NEW = "status IN ('planned', 'active', 'done', 'released')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE versions DROP CONSTRAINT IF EXISTS {_NAME}")
    op.create_check_constraint(_NAME, "versions", _NEW)
    # Dorovnanie už dokončených stavieb. K 03.09.2026 sú štyri (nex-productcatalogs v0.1.0,
    # nex-shopify v0.1.0 a v0.2.0, nex-websites v0.1.0) — všetky s priebehom ``done/done``
    # a všetky nesúce ``planned``. Bez tohto kroku by ostali vyzerať ako nezačaté aj po oprave.
    # Zámerne LEN ``planned``: ``active`` sa nedotýkame (mohla by práve bežať) a ``released``
    # je ďalej než ``done``.
    op.execute(
        """
        UPDATE versions v SET status = 'done'
        WHERE v.status = 'planned'
          AND EXISTS (
              SELECT 1 FROM pipeline_state ps
              WHERE ps.version_id = v.id AND ps.current_stage = 'done' AND ps.status = 'done'
          )
        """
    )


def downgrade() -> None:
    # Najprv dáta, potom obmedzenie — inak by re-add padol na riadkoch s 'done'.
    op.execute("UPDATE versions SET status = 'active' WHERE status = 'done'")
    op.execute(f"ALTER TABLE versions DROP CONSTRAINT IF EXISTS {_NAME}")
    op.create_check_constraint(_NAME, "versions", _OLD)
