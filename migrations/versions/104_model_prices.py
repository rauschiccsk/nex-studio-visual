"""Model price list from Claude Code instead of manual prices in Nastavenia (ICCINT-168, v4.43.0).

**What happened.** Náklady counted only input and output tokens at prices the Manažér entered manually
in Nastavenia. Cache tokens (the agent re-reads the whole conversation at every step) the cockpit
did not see at all — for Dedo Home 0.1.0 2.27 billion read and 17.5 million written — and the manual prices
did not match the models (Haiku output 3 instead of 5, Sonnet 5.5 input 3 instead of 2, Opus 5.5 4/20, not 5/25).

**What it adds.** The ``model_prices`` table: each model's price list in dollars (input, output, cache read and write),
read off paid turns, with the ECB rate, date and rate source.

**What it removes.** The manual ``api_price_*`` prices from ``system_settings`` — Director 06.10.2026: „Ak tie ceny
nepoužívame už prosím ich upratať, je zmetkujúce ak niečo tam je uvedené a v skutočnosti sa používa niečo
iné" The cockpit no longer computes anything with them.

**Downgrade** drops the table. The deleted manual prices do not return — after going back to an older version they
must be entered again (before deletion they were: Haiku 1/3, Sonnet 3/10, Opus 5/25, unknown model 10/50 € per million).

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
