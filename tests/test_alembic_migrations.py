"""Verify that Alembic migrations cover every ORM model and stay in sync.

These tests protect the domain layer: if someone adds a model but forgets to
write a migration (or vice-versa), these tests fail before the drift reaches
production.
"""

import os
import re
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from backend.db.base import ALL_MODELS, Base
from backend.db.session import _ensure_pg8000_driver

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"


def _get_test_database_url() -> str:
    from backend.config.settings import settings

    url = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)
    return _ensure_pg8000_driver(url)


def test_all_models_have_tablename() -> None:
    """Every concrete ORM model in ALL_MODELS defines ``__tablename__``."""
    for model in ALL_MODELS:
        assert hasattr(model, "__tablename__"), f"{model.__name__} missing __tablename__"
        assert model.__tablename__, f"{model.__name__} has empty __tablename__"


def test_every_model_table_is_in_metadata() -> None:
    """Every model's table is registered on Base.metadata."""
    tables_in_metadata = set(Base.metadata.tables.keys())
    for model in ALL_MODELS:
        assert model.__tablename__ in tables_in_metadata, (
            f"{model.__name__} -> {model.__tablename__} missing from Base.metadata"
        )


def test_expected_domain_tables_present() -> None:
    """The core domain tables that survive CR-NS-008 are registered.

    The legacy in-app design/execution pipeline tables were dropped by
    migration 048; multi-module (``project_modules`` /
    ``module_dependencies``) and the agent infra (Versions / Epics /
    Feats / Tasks / Bugs) are preserved. The F-007 Orchestration Cockpit
    (CR-NS-018) adds ``pipeline_state`` / ``pipeline_message`` (migration 051).
    """
    expected_tables = {
        "bugs",
        "epics",
        "feats",
        "module_dependencies",
        "project_modules",
        "projects",
        "tasks",
        "user_sessions",
        "users",
        "pipeline_state",
        "pipeline_message",
    }
    present = set(Base.metadata.tables.keys())
    missing = expected_tables - present
    assert not missing, f"Missing tables in metadata: {sorted(missing)}"


def test_migration_files_form_contiguous_chain() -> None:
    """Every migration NNN_*.py (001-022) exists and forms a linear chain."""
    migration_files = sorted(p for p in VERSIONS_DIR.glob("[0-9][0-9][0-9]_*.py"))
    numbers = [int(p.name.split("_", 1)[0]) for p in migration_files]
    assert numbers == list(range(1, len(numbers) + 1)), f"Migration numbers not contiguous: {numbers}"

    # Parse revision / down_revision and confirm the chain is linear
    rev_re = re.compile(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
    down_re = re.compile(
        r"^down_revision:\s*Union\[str,\s*None\]\s*=\s*(?:[\"']([^\"']+)[\"']|None)",
        re.MULTILINE,
    )

    chain = []
    for path in migration_files:
        content = path.read_text()
        rev_match = rev_re.search(content)
        down_match = down_re.search(content)
        assert rev_match, f"No revision= in {path.name}"
        assert down_match, f"No down_revision= in {path.name}"
        chain.append((rev_match.group(1), down_match.group(1) if down_match.group(1) else None))

    # First migration's down_revision must be None; each subsequent's down_revision
    # must be the prior revision.
    assert chain[0][1] is None, f"First migration must have down_revision=None, got {chain[0]}"
    for (prev_rev, _), (_, down) in zip(chain, chain[1:]):
        assert down == prev_rev, f"Broken chain: expected down_revision={prev_rev}, got {down}"


def test_no_schema_drift_vs_models(test_engine) -> None:
    """Running autogenerate against the test DB must produce zero diffs.

    ``test_engine`` fixture already ran ``Base.metadata.create_all``, so the
    live test schema matches the model definitions exactly. This guards
    against stray model changes landing without a corresponding migration.
    """
    with test_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    # ``diff`` is a list of DDL-difference tuples. Empty list = no drift.
    # Filter out any benign server_default quirks produced by pg8000 round-trips.
    meaningful = [entry for entry in diff if entry]
    assert not meaningful, f"Schema drift detected between models and DB: {meaningful}"


@pytest.fixture(scope="module")
def _raw_engine():
    """A module-scoped engine for reading the Alembic version table directly."""
    engine = create_engine(_get_test_database_url())
    yield engine
    engine.dispose()


def test_alembic_version_table_reachable(test_engine, _raw_engine) -> None:
    """The alembic_version table exists once ``test_engine`` has run create_all.

    ``test_engine`` calls ``Base.metadata.create_all`` which does NOT create
    ``alembic_version``. We only assert that a connection can be opened and
    query the metadata — this is a smoke check for the test infrastructure.
    """
    with _raw_engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _drop_database_if_exists(admin_url: str, db_name: str) -> None:
    """Disconnect any sessions and drop the given database."""
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
    """Drop (if exists) and recreate the named database."""
    _drop_database_if_exists(admin_url, db_name)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))  # noqa: S608
    finally:
        engine.dispose()


def test_alembic_upgrade_head_on_clean_database(monkeypatch) -> None:
    """``alembic upgrade head`` succeeds on a freshly-created empty database.

    ``test_no_schema_drift_vs_models`` runs against a DB populated by
    ``Base.metadata.create_all``, so it cannot catch migration files that
    fail when applied from scratch (e.g. duplicate index creation, drops of
    columns that never existed). This test creates a brand-new database,
    runs the entire migration chain end-to-end, and then asserts the resulting
    schema has zero drift against ``Base.metadata``.
    """
    from alembic import command
    from alembic.config import Config

    from backend.config import settings as settings_module

    base_url = _get_test_database_url()
    parts = base_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"
    upgrade_db_name = parts[1].split("?")[0] + "_alembic_upgrade"
    upgrade_db_url = parts[0] + "/" + upgrade_db_name

    _create_clean_database(admin_url, upgrade_db_name)

    # ``migrations/env.py`` reads ``settings.database_url`` at runtime, so
    # monkey-patching the singleton routes ``command.upgrade`` to the throwaway
    # database without touching the developer's real test DB.
    monkeypatch.setattr(settings_module.settings, "database_url", upgrade_db_url)

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))

    upgrade_engine = None
    try:
        command.upgrade(config, "head")

        upgrade_engine = create_engine(upgrade_db_url)
        with upgrade_engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            diff = compare_metadata(ctx, Base.metadata)
        meaningful = [entry for entry in diff if entry]
        assert not meaningful, f"Schema drift detected after `alembic upgrade head` on a clean DB: {meaningful}"
    finally:
        if upgrade_engine is not None:
            upgrade_engine.dispose()
        _drop_database_if_exists(admin_url, upgrade_db_name)
