"""Project archetype + mandatory auth-mode for v2.0.0 (CR-V2-005).

The v1 ``projects.category`` (``'singlemodule'`` / ``'multimodule'``) is replaced
by the v2 archetype model:

* ``projects.category`` → ``projects.type`` (``'standard'`` / ``'web'``) — a
  project archetype is a preset SURFACE COMPOSITION (design §4.2), no longer a
  module-count classifier. The column is RENAMED (data preserved), its values
  are data-migrated, and the CHECK is swapped (``ck_projects_category`` →
  ``ck_projects_type``).
* NEW ``projects.auth_mode`` (``'password'`` / ``'token'``) — mandatory login
  flavour wired onto every surface (``ck_projects_auth_mode``).

This mirrors ``backend/db/models/projects.py`` (``type`` + ``auth_mode`` columns
and their CHECKs) so the DB CHECKs and the Pydantic ``Literal`` schemas stay in
lock-step (the enum-tuple-single-source pattern).

Data migration (no-op on a fresh/CI DB, transforms any live dev-branch rows):

* ``type``: ``'singlemodule'`` → ``'standard'``; any remaining non-``'web'``
  value (e.g. the retired ``'multimodule'``) → ``'standard'`` (multimodule is
  removed in CR-V2-002; ``'web'`` is not inferable from a v1 category, so the
  conservative default is ``'standard'``).
* ``auth_mode``: legacy rows default to ``'password'`` (NEX-Studio-style
  username+password login) — a sensible default for the existing
  internal-tool projects. The column is added with a temporary
  ``server_default='password'`` so the NOT-NULL backfill is atomic, then the
  server_default is DROPPED so future inserts must supply ``auth_mode``
  explicitly (it is mandatory at the application layer).

Order matters: rename + data-migrate BEFORE re-creating the CHECK, so the
constraint validation never fails on live data.

Idempotent: drops use ``IF EXISTS`` (mirrors migration 069/070/071) so a re-run
— or a fresh DB whose ``create_all`` already built the v2 columns/CHECKs — never
errors. ``downgrade`` restores the v1 ``category`` column + CHECK and drops
``auth_mode``.

Revision ID: 072
Revises: 071
Create Date: 2026-06-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """True if ``table.column`` is present — guards the rename/add for idempotency
    (a fresh DB whose ``create_all`` already built the v2 shape has ``type`` not
    ``category``)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    # 1. category → type: rename the column (preserves data) only if the v1 column
    #    is still present (idempotent on a fresh v2 DB).
    if _column_exists("projects", "category") and not _column_exists("projects", "type"):
        op.alter_column("projects", "category", new_column_name="type", existing_type=sa.String(20))

    # 2. Data-migrate values BEFORE re-creating the CHECK. singlemodule → standard;
    #    any remaining non-'web' value (incl. the retired 'multimodule') → standard.
    op.execute("UPDATE projects SET type = 'standard' WHERE type = 'singlemodule'")
    op.execute("UPDATE projects SET type = 'standard' WHERE type NOT IN ('standard', 'web')")

    # 3. Swap the CHECK: drop the v1 category CHECK (IF EXISTS), add the type CHECK.
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_category")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_type")
    op.create_check_constraint("ck_projects_type", "projects", "type IN ('standard', 'web')")

    # 4. Add the mandatory auth_mode column. A temporary server_default backfills
    #    legacy rows atomically (NOT NULL), then the default is dropped so future
    #    inserts must supply auth_mode explicitly (mandatory at the app layer).
    if not _column_exists("projects", "auth_mode"):
        op.add_column(
            "projects",
            sa.Column("auth_mode", sa.String(20), nullable=False, server_default="password"),
        )
        op.alter_column("projects", "auth_mode", server_default=None)

    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_auth_mode")
    op.create_check_constraint("ck_projects_auth_mode", "projects", "auth_mode IN ('password', 'token')")


def downgrade() -> None:
    # Reverse order: drop auth_mode (+ its CHECK), then restore category from type.
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_auth_mode")
    if _column_exists("projects", "auth_mode"):
        op.drop_column("projects", "auth_mode")

    # type → category: rename back, restore the v1 CHECK. No reverse value mapping
    # is possible ('standard' came from either 'singlemodule' or the retired
    # 'multimodule'); restore as 'singlemodule' (the surviving v1 default) so the
    # v1 CHECK validates.
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_type")
    if _column_exists("projects", "type") and not _column_exists("projects", "category"):
        op.alter_column("projects", "type", new_column_name="category", existing_type=sa.String(20))
    op.execute("UPDATE projects SET category = 'singlemodule' WHERE category NOT IN ('singlemodule', 'multimodule')")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_category")
    op.create_check_constraint("ck_projects_category", "projects", "category IN ('singlemodule', 'multimodule')")
