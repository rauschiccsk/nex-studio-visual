"""Dedo vie pripraviť zadanie pre prácu, ktorá sa ešte nezačala (ICCINT-152).

**Čo sa dialo.** Dedove dvere do kokpitu majú návrh — text, ktorý Manažér v kokpite prečíta, prípadne
upraví a jedným tlačidlom pošle ďalej. Viazal sa ale na BEŽIACU stavbu, takže sa otvoril až vtedy, keď
už zadanie netreba. 25.09.2026 prestalo na MÁGERSTAVE fungovať spúšťanie NEX Inboxu z NEX Managera,
Director požiadal *„zapíš to zadanie do kokpitu ako návrh"* — a nešlo to. Text som mu musel podať do
ruky, aby ho pri spúšťaní rýchlej opravy vložil. Presne tomu mali tie dvere zabrániť.

**Prečo vlastná tabuľka.** Návrh do bežiacej stavby je správa v jej denníku a žije v
``pipeline_message``. Tá vyžaduje ``stage`` obmedzený na fázy priebehu — a pred stavbou žiadna fáza
neplatí. Zapísať tam ``priprava`` by bol vymyslený údaj v zázname, ktorý sa nikdy neprepisuje.

**Najviac jeden otvorený návrh na projekt drží databáza**, nie poradie príkazov v službe: pravidlo,
ktoré stojí na jednej funkcii, padne pri druhom zapisovateľovi — a Manažér by videl dve zadania a
nevedel, ktoré platí.

Revision ID: 100
Revises: 099
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "100"
down_revision: Union[str, None] = "099"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dedo_project_proposal",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("resolved_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "proposed_action IN ('fast_fix', 'new_version')",
            name="ck_dedo_project_proposal_action",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'sent', 'rejected', 'superseded')",
            name="ck_dedo_project_proposal_status",
        ),
    )
    op.create_index(
        "ix_dedo_project_proposal_project_id",
        "dedo_project_proposal",
        ["project_id"],
    )
    op.create_index(
        "uq_dedo_project_proposal_open",
        "dedo_project_proposal",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'proposed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_dedo_project_proposal_open", table_name="dedo_project_proposal")
    op.drop_index("ix_dedo_project_proposal_project_id", table_name="dedo_project_proposal")
    op.drop_table("dedo_project_proposal")
