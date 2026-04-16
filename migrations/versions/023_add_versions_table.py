"""Create versions table and add version_id FK to epics and bugs.

This migration creates the ``versions`` table — the release-version container
for a project's Epics and Bugs — and adds nullable ``version_id`` foreign-key
columns to both ``epics`` and ``bugs`` with ``ON DELETE RESTRICT`` semantics.
An existing Epic or Bug may be left unassigned to any Version, but a Version
that still has Epics or Bugs referencing it cannot be deleted.

Revision ID: 023
Revises: 022
Create Date: 2026-04-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "versions",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="planned",
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'released')",
            name="ck_versions_status",
        ),
        sa.UniqueConstraint(
            "project_id",
            "version_number",
            name="uq_versions_project_id_version_number",
        ),
    )
    op.create_index(
        "ix_versions_project_id",
        "versions",
        ["project_id"],
        unique=False,
    )

    op.add_column(
        "epics",
        sa.Column("version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_epics_version_id_versions",
        "epics",
        "versions",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_epics_version_id",
        "epics",
        ["version_id"],
        unique=False,
    )

    op.add_column(
        "bugs",
        sa.Column("version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bugs_version_id_versions",
        "bugs",
        "versions",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_bugs_version_id",
        "bugs",
        ["version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bugs_version_id", table_name="bugs")
    op.drop_constraint("fk_bugs_version_id_versions", "bugs", type_="foreignkey")
    op.drop_column("bugs", "version_id")

    op.drop_index("ix_epics_version_id", table_name="epics")
    op.drop_constraint("fk_epics_version_id_versions", "epics", type_="foreignkey")
    op.drop_column("epics", "version_id")

    op.drop_index("ix_versions_project_id", table_name="versions")
    op.drop_table("versions")
