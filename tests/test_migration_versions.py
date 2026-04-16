"""Tests for migration 023 — versions table + epics/bugs.version_id FKs.

These tests run the Alembic chain up to revision ``023`` on a throwaway
PostgreSQL database, then assert:

* the ``versions`` table exists with the expected columns, constraints, and
  indexes;
* ``epics.version_id`` and ``bugs.version_id`` foreign-key columns exist with
  ``ON DELETE RESTRICT`` semantics;
* ``downgrade 022`` reverts every object created by the migration.

The throwaway database is created and dropped per-module so the developer's
regular test database is never touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.session import _ensure_pg8000_driver

REPO_ROOT = Path(__file__).resolve().parent.parent


def _get_test_database_url() -> str:
    from backend.config.settings import settings

    url = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)
    return _ensure_pg8000_driver(url)


def _drop_database_if_exists(admin_url: str, db_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))  # noqa: S608
    finally:
        engine.dispose()


def _create_clean_database(admin_url: str, db_name: str) -> None:
    _drop_database_if_exists(admin_url, db_name)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))  # noqa: S608
    finally:
        engine.dispose()


def _alembic_config(db_url: str, monkeypatch) -> Config:
    """Build an Alembic Config pointing at the throwaway DB."""
    from backend.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "database_url", db_url)

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture()
def upgraded_engine(monkeypatch) -> Engine:
    """Create a clean DB and run ``alembic upgrade 023`` against it."""
    base_url = _get_test_database_url()
    parts = base_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"
    db_name = parts[1].split("?")[0] + "_mig023"
    db_url = parts[0] + "/" + db_name

    _create_clean_database(admin_url, db_name)

    config = _alembic_config(db_url, monkeypatch)
    command.upgrade(config, "023")

    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()
        _drop_database_if_exists(admin_url, db_name)


def _get_fk(inspector, table: str, referred_table: str, local_col: str) -> dict:
    """Return the FK dict on ``table`` pointing to ``referred_table`` via ``local_col``."""
    for fk in inspector.get_foreign_keys(table):
        if fk["referred_table"] == referred_table and local_col in fk["constrained_columns"]:
            return fk
    raise AssertionError(
        f"No FK on {table}.{local_col} referencing {referred_table}; found: {inspector.get_foreign_keys(table)}"
    )


class TestMigration023Upgrade:
    """Migration 023 upgrade must produce the expected schema."""

    def test_versions_table_exists(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        assert "versions" in inspector.get_table_names()

    def test_versions_columns(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        cols = {c["name"]: c for c in inspector.get_columns("versions")}

        expected = {
            "id",
            "project_id",
            "version_number",
            "status",
            "description",
            "target_date",
            "release_date",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols.keys()), f"Missing columns: {expected - cols.keys()}"

        # Nullability expectations.
        assert cols["project_id"]["nullable"] is False
        assert cols["version_number"]["nullable"] is False
        assert cols["status"]["nullable"] is False
        assert cols["description"]["nullable"] is True
        assert cols["target_date"]["nullable"] is True
        assert cols["release_date"]["nullable"] is True

    def test_versions_unique_project_version_number(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        uniques = inspector.get_unique_constraints("versions")
        named = {uq["name"]: uq["column_names"] for uq in uniques}
        assert "uq_versions_project_id_version_number" in named
        assert named["uq_versions_project_id_version_number"] == [
            "project_id",
            "version_number",
        ]

    def test_versions_status_check_constraint(self, upgraded_engine: Engine) -> None:
        """Invalid status must be rejected; valid statuses must be accepted."""
        with upgraded_engine.begin() as conn:
            # Create a user + project to satisfy FKs.
            user_id = conn.execute(
                text(
                    "INSERT INTO users (id, username, email, password_hash, role) "
                    "VALUES (gen_random_uuid(), 'u_mig023', 'u_mig023@example.com', 'x', 'ri') "
                    "RETURNING id"
                )
            ).scalar()
            project_id = conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, category, description, created_by) "
                    "VALUES (gen_random_uuid(), 'P', 'p-mig023', 'singlemodule', 'd', :uid) "
                    "RETURNING id"
                ),
                {"uid": str(user_id)},
            ).scalar()

            # Valid statuses succeed.
            for status in ("planned", "active", "released"):
                conn.execute(
                    text(
                        "INSERT INTO versions (id, project_id, version_number, status) "
                        "VALUES (gen_random_uuid(), :pid, :vn, :st)"
                    ),
                    {"pid": str(project_id), "vn": f"v-{status}", "st": status},
                )

        # Invalid status rejected.
        with pytest.raises((IntegrityError, ProgrammingError)):
            with upgraded_engine.begin() as conn:
                user_id = conn.execute(
                    text(
                        "INSERT INTO users (id, username, email, password_hash, role) "
                        "VALUES (gen_random_uuid(), 'u_bad', 'u_bad@example.com', 'x', 'ri') "
                        "RETURNING id"
                    )
                ).scalar()
                project_id = conn.execute(
                    text(
                        "INSERT INTO projects (id, name, slug, category, description, created_by) "
                        "VALUES (gen_random_uuid(), 'P2', 'p-bad', 'singlemodule', 'd', :uid) "
                        "RETURNING id"
                    ),
                    {"uid": str(user_id)},
                ).scalar()
                conn.execute(
                    text(
                        "INSERT INTO versions (id, project_id, version_number, status) "
                        "VALUES (gen_random_uuid(), :pid, 'v', 'archived')"
                    ),
                    {"pid": str(project_id)},
                )

    def test_versions_fk_to_projects_cascade(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        fk = _get_fk(inspector, "versions", "projects", "project_id")
        options = fk.get("options") or {}
        assert options.get("ondelete", "").upper() == "CASCADE"

    def test_versions_index_project_id(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("versions")}
        assert "ix_versions_project_id" in idx_names

    def test_epics_version_id_column_added(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        cols = {c["name"]: c for c in inspector.get_columns("epics")}
        assert "version_id" in cols
        assert cols["version_id"]["nullable"] is True

    def test_epics_version_id_fk_restrict(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        fk = _get_fk(inspector, "epics", "versions", "version_id")
        options = fk.get("options") or {}
        assert options.get("ondelete", "").upper() == "RESTRICT"

    def test_epics_version_id_indexed(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("epics")}
        assert "ix_epics_version_id" in idx_names

    def test_bugs_version_id_column_added(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        cols = {c["name"]: c for c in inspector.get_columns("bugs")}
        assert "version_id" in cols
        assert cols["version_id"]["nullable"] is True

    def test_bugs_version_id_fk_restrict(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        fk = _get_fk(inspector, "bugs", "versions", "version_id")
        options = fk.get("options") or {}
        assert options.get("ondelete", "").upper() == "RESTRICT"

    def test_bugs_version_id_indexed(self, upgraded_engine: Engine) -> None:
        inspector = inspect(upgraded_engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("bugs")}
        assert "ix_bugs_version_id" in idx_names

    def test_version_restrict_blocks_delete_with_epic(self, upgraded_engine: Engine) -> None:
        """Deleting a version referenced by an epic must raise FK violation."""
        with upgraded_engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (id, username, email, password_hash, role) "
                    "VALUES (gen_random_uuid(), 'u_ep', 'u_ep@example.com', 'x', 'ri') "
                    "RETURNING id"
                )
            ).scalar()
            project_id = conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, category, description, created_by) "
                    "VALUES (gen_random_uuid(), 'P', 'p-ep', 'singlemodule', 'd', :uid) "
                    "RETURNING id"
                ),
                {"uid": str(user_id)},
            ).scalar()
            version_id = conn.execute(
                text(
                    "INSERT INTO versions (id, project_id, version_number) "
                    "VALUES (gen_random_uuid(), :pid, 'v1.0') RETURNING id"
                ),
                {"pid": str(project_id)},
            ).scalar()
            conn.execute(
                text(
                    "INSERT INTO epics (id, project_id, number, title, status, version_id) "
                    "VALUES (gen_random_uuid(), :pid, 1, 'E', 'planned', :vid)"
                ),
                {"pid": str(project_id), "vid": str(version_id)},
            )

        with pytest.raises((IntegrityError, ProgrammingError)):
            with upgraded_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM versions WHERE id = :id"),
                    {"id": str(version_id)},
                )

    def test_version_restrict_blocks_delete_with_bug(self, upgraded_engine: Engine) -> None:
        """Deleting a version referenced by a bug must raise FK violation."""
        with upgraded_engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (id, username, email, password_hash, role) "
                    "VALUES (gen_random_uuid(), 'u_bg', 'u_bg@example.com', 'x', 'ri') "
                    "RETURNING id"
                )
            ).scalar()
            project_id = conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, category, description, created_by) "
                    "VALUES (gen_random_uuid(), 'P', 'p-bg', 'singlemodule', 'd', :uid) "
                    "RETURNING id"
                ),
                {"uid": str(user_id)},
            ).scalar()
            version_id = conn.execute(
                text(
                    "INSERT INTO versions (id, project_id, version_number) "
                    "VALUES (gen_random_uuid(), :pid, 'v2.0') RETURNING id"
                ),
                {"pid": str(project_id)},
            ).scalar()
            conn.execute(
                text(
                    "INSERT INTO bugs (id, project_id, bug_number, title, description, "
                    "severity, created_by, version_id) "
                    "VALUES (gen_random_uuid(), :pid, 1, 'B', 'd', 'minor', :uid, :vid)"
                ),
                {
                    "pid": str(project_id),
                    "uid": str(user_id),
                    "vid": str(version_id),
                },
            )

        with pytest.raises((IntegrityError, ProgrammingError)):
            with upgraded_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM versions WHERE id = :id"),
                    {"id": str(version_id)},
                )


class TestMigration023Downgrade:
    """``alembic downgrade 022`` must remove every object added by migration 023."""

    def test_downgrade_removes_everything(self, monkeypatch) -> None:
        base_url = _get_test_database_url()
        parts = base_url.rsplit("/", 1)
        admin_url = parts[0] + "/postgres"
        db_name = parts[1].split("?")[0] + "_mig023_down"
        db_url = parts[0] + "/" + db_name

        _create_clean_database(admin_url, db_name)
        engine = create_engine(db_url)

        try:
            config = _alembic_config(db_url, monkeypatch)

            command.upgrade(config, "023")
            # Sanity: versions table is present after upgrade.
            insp = inspect(engine)
            assert "versions" in insp.get_table_names()
            assert "version_id" in {c["name"] for c in insp.get_columns("epics")}
            assert "version_id" in {c["name"] for c in insp.get_columns("bugs")}

            command.downgrade(config, "022")

            insp = inspect(engine)
            assert "versions" not in insp.get_table_names()
            assert "version_id" not in {c["name"] for c in insp.get_columns("epics")}
            assert "version_id" not in {c["name"] for c in insp.get_columns("bugs")}

            # Indexes on epics/bugs for version_id are gone too.
            epic_idx = {idx["name"] for idx in insp.get_indexes("epics")}
            bug_idx = {idx["name"] for idx in insp.get_indexes("bugs")}
            assert "ix_epics_version_id" not in epic_idx
            assert "ix_bugs_version_id" not in bug_idx
        finally:
            engine.dispose()
            _drop_database_if_exists(admin_url, db_name)


class TestMigration023File:
    """Static checks on the migration file itself."""

    def test_migration_file_exists(self) -> None:
        path = REPO_ROOT / "migrations" / "versions" / "023_add_versions_table.py"
        assert path.is_file(), f"Expected migration file at {path}"

    def test_migration_revision_identifiers(self) -> None:
        path = REPO_ROOT / "migrations" / "versions" / "023_add_versions_table.py"
        content = path.read_text()
        assert 'revision: str = "023"' in content
        assert 'down_revision: Union[str, None] = "022"' in content
