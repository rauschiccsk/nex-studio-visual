"""Cenník modelov z Claude Code namiesto ručných cien v Nastaveniach (ICCINT-168, v4.43.0).

**Čo sa dialo.** Náklady počítali len vstupné a výstupné tokeny podľa cien, ktoré Manažér zadal ručne
v Nastaveniach. Tokeny z vyrovnávacej pamäte (agent pri každom kroku znova číta celý rozhovor) kokpit
nevidel vôbec — pri Dedo Home 0.1.0 2,27 miliardy prečítaných a 17,5 milióna zapísaných — a ručné ceny
nesedeli s modelmi (Haiku výstup 3 namiesto 5, Sonnet 5.5 vstup 3 namiesto 2, Opus 5.5 4/20, nie 5/25).

**Čo pridáva.** Tabuľku ``model_prices``: cenník každého modelu v dolároch (vstup, výstup, čítanie a zápis
vyrovnávacej pamäte), vyčítaný zo zaplatených ťahov, s kurzom ECB, dátumom a zdrojom kurzu.

**Čo odoberá.** Ručné ceny ``api_price_*`` zo ``system_settings`` — Director 06.10.2026: „Ak tie ceny
nepoužívame už prosím ich upratať, je zmetkujúce ak niečo tam je uvedené a v skutočnosti sa používa niečo
iné." Kokpit nimi odteraz nepočíta nič.

**Späť** sa tabuľka zahodí. Vymazané ručné ceny sa nevrátia — po návrate na staršiu verziu ich treba zadať
znova (pred zmazaním boli: Haiku 1/3, Sonnet 3/10, Opus 5/25, neznámy model 10/50 € za milión).

Revision ID: 104
Revises: 103
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "104"
down_revision: Union[str, None] = "103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_prices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("input_usd", sa.Float, nullable=False),
        sa.Column("output_usd", sa.Float, nullable=False),
        sa.Column("cache_read_usd", sa.Float, nullable=False),
        sa.Column("cache_write_usd", sa.Float, nullable=False),
        sa.Column("web_search_usd", sa.Float, nullable=True),
        sa.Column("eur_usd", sa.Float, nullable=True),
        sa.Column("rate_date", sa.Date, nullable=True),
        sa.Column("rate_source", sa.String(200), nullable=True),
        sa.Column("observations", sa.Integer, nullable=False),
        sa.Column("max_deviation", sa.Float, nullable=False),
        sa.Column("checked_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("model", "valid_from", name="uq_model_prices_model_valid_from"),
    )
    op.execute("DELETE FROM system_settings WHERE key LIKE 'api\\_price\\_%'")


def downgrade() -> None:
    op.drop_table("model_prices")
