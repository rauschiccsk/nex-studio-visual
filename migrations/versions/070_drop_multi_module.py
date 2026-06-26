"""Drop multi-module domain for v2.0.0 (CR-V2-002).

The v1 multi-module model (a project = ≥1 module, with inter-module dependency
edges) is removed in v2: a project is now a single backend + one-or-more
frontend SURFACES (design §4.2), not a registry of modules. This migration
tears down the three multi-module schema artefacts:

* ``epics.module_id`` column (+ its ``ix_epics_module_id`` index and the
  ``module_id → project_modules.id`` FK) — epics are now always project-level.
* ``module_dependencies`` table (child: FKs into ``project_modules``).
* ``project_modules`` table (parent).

Drop order respects the FKs — the child (``module_dependencies``) and the
referencing column (``epics.module_id``) go BEFORE the parent
(``project_modules``), so no FK rejects the drop.

NOTE: ``Project.category`` (``'singlemodule'``/``'multimodule'``) is deliberately
LEFT untouched here — CR-V2-005 replaces it with ``type`` (+ ``auth_mode``) in
its own migration (072). This migration removes only the module ENTITIES.

``upgrade`` uses ``IF EXISTS`` on the column/index drops for idempotency (a
re-run, or a fresh DB whose ``create_all`` never built these, never errors).
``downgrade`` faithfully recreates the FINAL v1 shape (post-031/032: kebab-case
``code VARCHAR(50)`` + category/code-format CHECKs), but does NOT restore any
data (the tables come back empty) — documented, consistent with 069.

Revision ID: 070
Revises: 069
Create Date: 2026-06-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop epics.module_id (the referencing column) first: its index, then
    #    the column itself (Postgres drops the dependent FK with the column).
    #    Raw SQL ``IF EXISTS`` keeps the column drop idempotent — ``op.drop_column``
    #    has no ``if_exists`` kwarg in this Alembic, so mirror 069's raw-SQL pattern.
    op.drop_index("ix_epics_module_id", table_name="epics", if_exists=True)
    op.execute("ALTER TABLE epics DROP COLUMN IF EXISTS module_id")

    # 2. Drop the child table (module_dependencies → project_modules) before the
    #    parent. drop_table cascades each table's own indexes.
    op.drop_table("module_dependencies")

    # 3. Drop the parent table.
    op.drop_table("project_modules")


def downgrade() -> None:
    # Recreate the parent (project_modules) in its FINAL v1 shape: code is
    # kebab-case VARCHAR(50) (post-032) with the category + code-format CHECKs
    # (031/032). No data is restored — the table comes back empty.
    op.create_table(
        "project_modules",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("design_doc_path", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("project_id", "code", name="uq_project_modules_project_id_code"),
        sa.CheckConstraint(
            "status IN ('planned', 'in_design', 'in_development', 'done')",
            name="ck_project_modules_status",
        ),
        sa.CheckConstraint(
            "category IN ('Systém', 'Katalógy', 'Sklad', 'Predaj', 'Nákup', 'Účtovníctvo', 'Pokladňa')",
            name="ck_project_modules_category",
        ),
        sa.CheckConstraint(
            r"code ~ '^[a-z][a-z0-9-]*[a-z0-9]$'",
            name="ck_project_modules_code_format",
        ),
    )
    op.create_index("ix_project_modules_project_id", "project_modules", ["project_id"])

    # Recreate the child (module_dependencies → project_modules).
    op.create_table(
        "module_dependencies",
        sa.Column("module_id", sa.UUID(), nullable=False),
        sa.Column("depends_on_module_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["module_id"], ["project_modules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_module_id"], ["project_modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "module_id",
            "depends_on_module_id",
            name="uq_module_dependencies_module_id_depends_on_module_id",
        ),
    )
    op.create_index("ix_module_dependencies_module_id", "module_dependencies", ["module_id"])
    op.create_index(
        "ix_module_dependencies_depends_on_module_id",
        "module_dependencies",
        ["depends_on_module_id"],
    )

    # Re-add epics.module_id (nullable, ON DELETE SET NULL) + its index.
    op.add_column("epics", sa.Column("module_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "epics_module_id_fkey",
        "epics",
        "project_modules",
        ["module_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_epics_module_id", "epics", ["module_id"])
