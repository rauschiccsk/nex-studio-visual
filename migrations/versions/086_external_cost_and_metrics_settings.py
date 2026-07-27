"""Create ``external_cost`` + collapse the metrics coefficient settings (CR-V2-063).

Two halves of one change on the Náklady screen:

1. **Schema** — ``external_cost`` holds the hand-entered token spend the cockpit cannot meter (work
   done outside a build: Dedo in the terminal, a developer working directly). ``version_id`` NULL is a
   project-level entry; a ``version_id`` that belongs to ANOTHER project is not a DB constraint (a
   composite FK would need a redundant column) — the router validates it and returns 422.
2. **Data** — the five per-phase ``metrics_minutes_per_mtok_{phase}`` coefficients collapse into ONE
   ``metrics_minutes_per_mtok`` (seeded with the Manažér-calibrated ``600``), and the manually entered
   external row gets its own wage ``metrics_hourly_wage_externe``. ``developer_hourly_rate`` is deleted
   as dead — no code reads it, while its description still claimed it drove the metrics page.

The stored ``api_price_*`` VALUES are deliberately left untouched even though their unit changed from
``$`` to ``€`` — a silent × rate would be worse than the mismatch; the Manažér re-enters them.

Idempotent: ``IF NOT EXISTS`` / ``IF EXISTS`` + ``WHERE NOT EXISTS`` guards so a re-run (or a clean DB)
never errors, and ``downgrade()`` reverses both halves (up→down→up leaves no orphan rows) WITHOUT losing
the calibrated coefficient — the five per-phase keys come back carrying the collapsed key's live value.

Revision ID: 086
Revises: 085
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "086"
down_revision: Union[str, None] = "085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The five per-phase coefficients this CR collapses into ``metrics_minutes_per_mtok`` — deleted on
#: upgrade, re-inserted on downgrade with the value the collapsed key still holds (the calibrated
#: number is NOT lost by going back).
_RETIRED_COEFFICIENT_KEYS: tuple[str, ...] = (
    "metrics_minutes_per_mtok_priprava",
    "metrics_minutes_per_mtok_navrh",
    "metrics_minutes_per_mtok_vizual",
    "metrics_minutes_per_mtok_programovanie",
    "metrics_minutes_per_mtok_verifikacia",
)

#: Dead keys this CR deletes — nothing reads them, so a downgrade restores them merely UNSET ('0.0');
#: there is no value to give back.
_DEAD_KEYS: tuple[str, ...] = ("developer_hourly_rate",)

#: Everything the upgrade deletes.
_RETIRED_KEYS: tuple[str, ...] = (*_RETIRED_COEFFICIENT_KEYS, *_DEAD_KEYS)

#: The ONE coefficient the five per-phase keys collapse into — also the value a downgrade gives back.
_COLLAPSED_COEFFICIENT_KEY = "metrics_minutes_per_mtok"

#: The two keys this CR introduces — (key, value) seeded on upgrade, deleted on downgrade.
_ADDED_KEYS: tuple[tuple[str, str], ...] = (
    (_COLLAPSED_COEFFICIENT_KEY, "600"),
    ("metrics_hourly_wage_externe", "0.0"),
)


def _insert_setting(key: str, value: str) -> None:
    """Seed one float setting, skipping it when a row already exists (never clobber a stored value)."""
    op.execute(
        sa.text(
            "INSERT INTO system_settings (key, value, value_type) "
            "SELECT :key, :value, 'float' "
            "WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE key = :key)"
        ).bindparams(key=key, value=value)
    )


def _delete_settings(keys: Sequence[str]) -> None:
    for key in keys:
        op.execute(sa.text("DELETE FROM system_settings WHERE key = :key").bindparams(key=key))


def _collapsed_coefficient() -> str:
    """The live value of the collapsed coefficient, for the downgrade to hand BACK to the five per-phase
    keys. Seeding them '0.0' would destroy the Manažér-calibrated number on a mere up→down; '0.0' is the
    fallback only when the collapsed key is absent (nothing to give back)."""
    value = (
        op.get_bind()
        .execute(
            sa.text("SELECT value FROM system_settings WHERE key = :key"),
            {"key": _COLLAPSED_COEFFICIENT_KEY},
        )
        .scalar()
    )
    return str(value) if value is not None else "0.0"


def upgrade() -> None:
    op.create_table(
        "external_cost",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: deleting a version must never erase hand-entered money from the project
        # total — the entry survives and degrades to a project-level one (NULL already means exactly that).
        sa.ForeignKeyConstraint(["version_id"], ["versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name="ck_external_cost_tokens_nonneg",
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index("ix_external_cost_project_id", "external_cost", ["project_id"], if_not_exists=True)
    op.create_index("ix_external_cost_version_id", "external_cost", ["version_id"], if_not_exists=True)

    for key, value in _ADDED_KEYS:
        _insert_setting(key, value)
    _delete_settings(_RETIRED_KEYS)


def downgrade() -> None:
    # Read the collapsed value BEFORE deleting it: the five per-phase coefficients get it back, so an
    # up→down leaves the calibrated number in place instead of wiping it to "not configured". The dead
    # developer rate has no value to restore and returns UNSET ('0.0').
    coefficient = _collapsed_coefficient()
    for key in _RETIRED_COEFFICIENT_KEYS:
        _insert_setting(key, coefficient)
    for key in _DEAD_KEYS:
        _insert_setting(key, "0.0")
    _delete_settings([key for key, _ in _ADDED_KEYS])

    op.drop_index("ix_external_cost_version_id", table_name="external_cost", if_exists=True)
    op.drop_index("ix_external_cost_project_id", table_name="external_cost", if_exists=True)
    op.drop_table("external_cost", if_exists=True)
