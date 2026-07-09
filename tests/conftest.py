"""Pytest configuration with SAVEPOINT transaction isolation.

Uses TEST_DATABASE_URL — NEVER the production DATABASE_URL.
Each test runs in a savepoint that is rolled back after the test,
keeping the test database clean without costly create/drop cycles.
"""

import os
from pathlib import Path

# Set required env vars BEFORE any backend imports trigger Settings() instantiation
os.environ.setdefault("GITHUB_TOKEN", "ghp_test_dummy_token")

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.config.settings import settings

# ``backend.db.base`` imports every ORM model — importing it here populates
# ``Base.metadata`` with every table at collection time, so a model missing from
# the registry surfaces as an import error here. The test schema itself is now
# built from the migration chain (``_reset_test_schema_to_head``), not
# ``Base.metadata.create_all``; the import is kept for that registration guard.
from backend.db.base import Base  # noqa: F401

# Explicit model imports for ``Base.metadata`` awareness — required by the
# Model Generation Checklist. ``backend.db.base`` re-exports these, but each
# new model is listed explicitly here so missing registrations surface as
# import errors during test collection.
from backend.db.models.bugs import Bug  # noqa: F401
from backend.db.models.projects import Project  # noqa: F401
from backend.db.models.system_settings import SystemSetting  # noqa: F401
from backend.db.models.tasks import Epic  # noqa: F401
from backend.db.models.versions import Version  # noqa: F401
from backend.db.session import _ensure_pg8000_driver, get_db
from backend.main import app
from backend.services import template_bootstrap
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from tests._db_guard import assert_test_db_distinct


def _get_test_database_url() -> str:
    """Return the test database URL, ensuring pg8000 driver."""
    from backend.config.settings import settings

    url = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)
    return _ensure_pg8000_driver(url)


def _ensure_test_database_exists(test_url: str) -> None:
    """Create the test database if it does not exist.

    Connects to the default 'postgres' database to issue CREATE DATABASE.
    """
    # Derive admin URL by replacing the DB name with 'postgres'
    parts = test_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    db_name = parts[1].split("?")[0]  # strip query params if any

    with admin_engine.connect() as conn:
        result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name})
        if not result.scalar():
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))  # noqa: S608
    admin_engine.dispose()


def _reset_test_schema_to_head(url: str) -> None:
    """Reset the test DB to the v2 migration head (drop public schema + upgrade).

    The test database is a PERSISTENT/shared Postgres. ``Base.metadata.create_all``
    is a NO-OP for any table that already exists and never ALTERs a stale
    constraint, so a pre-existing v1 schema (e.g. the 2026-05-03
    ``ck_projects_category`` constraint) would survive untouched — tests would
    then run against a v1 schema while the models + migrations describe v2.

    Instead we make the schema authoritative against the migration chain:

    1. DROP + CREATE the ``public`` schema — wipes ANY stale tables/constraints
       (including ``alembic_version``) so there is no leftover v1 state.
    2. ``alembic upgrade head`` — replays migrations 001..074 (v2 head) against
       the SEPARATE test DB via the ``-x url=...`` override honoured by
       ``migrations/env.py``. The schema therefore matches exactly what a
       production migration produces, not merely the current ORM metadata.
    """
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    admin_engine.dispose()

    repo_root = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    # Routed to the test DB (NOT settings.database_url) via env.py's -x override.
    alembic_cfg.cmd_opts = type("opts", (), {"x": [f"url={url}"]})()
    command.upgrade(alembic_cfg, "head")

    # Purge the migration-seeded baseline data (migration 024 seeds the default
    # ``admin`` user + its ``user_sessions`` row). The migration chain gives us
    # the authoritative v2 SCHEMA; the seeded ADMIN ROW is production
    # environment data, not a test baseline. The whole suite is built on a
    # SAVEPOINT-per-test model where each test seeds the users/projects it
    # needs (``seed_user``, ``_seed_admin``, ``make_user``) into an otherwise
    # EMPTY DB — leaving the seeded ``admin`` in place would collide with every
    # ``seed_user(username="admin")``. Truncate it once here so each test starts
    # from "v2 schema + empty data", exactly as the fixtures expect.
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text("DELETE FROM user_sessions"))
        conn.execute(text("DELETE FROM users"))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def test_engine():
    """Create a SQLAlchemy engine for the test database (session-scoped).

    The schema is brought to the v2 migration head (see
    ``_reset_test_schema_to_head``) rather than via ``Base.metadata.create_all``,
    so the persistent test DB always reflects migrations 001..074 and never a
    stale v1 schema.
    """
    from backend.config.settings import settings

    url = _get_test_database_url()

    # CR-NS-076: refuse to even connect if the test DB is not a DISTINCT
    # database from production. This MUST run before
    # ``_ensure_test_database_exists`` / the schema reset below — otherwise a
    # mis-set ``TEST_DATABASE_URL`` pointing at the cockpit DB would have its
    # schema DROPPED and re-migrated. Guarding here (rather than only in the
    # autouse fixture, which depends on this fixture and therefore runs after
    # setup) closes that window and also covers ``backend/tests``, which
    # re-imports this fixture but not the autouse one below.
    assert_test_db_distinct(settings.database_url, url)

    _ensure_test_database_exists(url)

    # Reset the persistent test DB to the v2 migration head before any test
    # touches it. ``Base`` is imported above purely so every ORM model is
    # registered on import (collection-time guard); the schema itself comes
    # from the migration chain, not ``create_all``.
    _reset_test_schema_to_head(url)

    engine = create_engine(url, pool_pre_ping=True)

    yield engine

    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _guard_prod_db_isolation(test_engine):
    """Guarantee no test can EVER write to the cockpit/PROD database (CR-NS-076).

    1. Hard-abort the run if ``TEST_DATABASE_URL`` is not a distinct database
       from the production ``settings.database_url``. ``test_engine`` already
       enforces this before touching any DB; re-asserting here keeps the
       documented isolation gate co-located with the rebind.
    2. Rebind the shared ``SessionLocal`` sessionmaker (and the module-level
       ``engine``) to the test engine for the whole session. ``SessionLocal``
       is imported BY REFERENCE everywhere, so reconfiguring the single shared
       object redirects even an un-monkeypatched ``SessionLocal()`` to the test
       DB — never the cockpit DB — closing the leak that put a full pipeline
       tree into ``nexstudio`` on 2026-06-08.

    Per-test ``monkeypatch.setattr(<module>, "SessionLocal", ...)`` overrides
    still work; they just replace an already-test-bound factory. Production
    ``backend/db/session.py`` behaviour is untouched — only the live, shared
    object is reconfigured for the duration of the test session and restored
    on teardown.

    3. Neutralise the FastAPI lifespan's ``_run_alembic_upgrade``. Every
       ``TestClient(app)`` enters the app lifespan, which calls
       ``backend.main._run_alembic_upgrade`` — and that builds its OWN Alembic
       ``Config("alembic.ini")`` that resolves to the PRODUCTION
       ``settings.database_url`` (it does NOT go through the rebind above).
       During tests that both (a) runs migrations against the cockpit/PROD DB
       and (b) explodes on the legacy ``ck_projects_category`` row, which is
       exactly the v1 schema the v2 migrations remove. The test DB is already
       brought to head by ``test_engine`` (``_reset_test_schema_to_head``), so
       the lifespan migration is redundant here — replace it with a no-op for
       the session. Production startup behaviour is untouched.
    """
    from backend.config.settings import settings
    from backend.db import session as db_session_module
    from backend.main import _run_alembic_upgrade as _orig_run_alembic_upgrade

    assert_test_db_distinct(settings.database_url, _get_test_database_url())

    original_engine = db_session_module.engine
    db_session_module.SessionLocal.configure(bind=test_engine)
    db_session_module.engine = test_engine

    import backend.main as _main_module

    _main_module._run_alembic_upgrade = lambda: None

    yield

    # Restore process-global state exactly as we found it.
    _main_module._run_alembic_upgrade = _orig_run_alembic_upgrade
    db_session_module.SessionLocal.configure(bind=original_engine)
    db_session_module.engine = original_engine


@pytest.fixture()
def db_connection(test_engine):
    """Provide a connection with an open transaction that is rolled back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()

    yield connection

    transaction.rollback()
    connection.close()


@pytest.fixture()
def db_session(db_connection):
    """Provide a Session using SAVEPOINT isolation.

    session.commit() flushes but does NOT commit the outer transaction.
    After the test, the outer transaction is rolled back — all changes disappear.
    """
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
    )

    yield session

    session.close()


@pytest.fixture()
def client(db_session):
    """Provide a FastAPI TestClient with the DB dependency overridden.

    All endpoint tests should use this fixture to ensure requests
    hit the SAVEPOINT-isolated test database, not the production DB.

    Auth dependencies are NOT overridden globally here — that would
    break tests under ``tests/api/test_auth_*.py`` which deliberately
    assert the 401/403 paths. Per-router test files (``test_*_router.py``)
    inline their own RBAC overrides via the auto-patch applied during
    the M2.D RBAC roll-out (2026-05-07).
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # Live-doc writes (project create / task / feat / module) reindex into RAG.
    # Tests must never touch the real Qdrant/Ollama (reachable in this env) —
    # disable indexing by returning no indexer.
    app.dependency_overrides[get_rag_indexer] = lambda: None

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Create-Project KB isolation (docs/specs/kb-ghost-root-cause.md Fix 1 +
# kb-ghost-followup.md Fix A)
# ---------------------------------------------------------------------------
#
# Lives in the ROOT conftest (not tests/integration/) so EVERY create-touching
# module can reach it, regardless of directory:
#   * tests/integration/  — pulled in for the whole suite via an autouse wrapper
#     in tests/integration/conftest.py (the Fix-A coverage gap: test_auth_flow's
#     create SUCCESS path had NO isolation, so the ghost slug ``test-auth-project``
#     recurred whenever ``template_init_script_path`` was configured).
#   * tests/test_project_router.py, tests/api/test_project_create_validation.py,
#     tests/api/test_project_ports.py — opt in via
#     ``pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")``.
#
# Deliberately NOT autouse for the ENTIRE suite: KB/RAG read tests (e.g.
# tests/integration/test_knowledge_rag.py) legitimately point settings at their
# own tmp KB, and a blanket autouse would fight them. Scope stays on the
# create-touching paths.


@pytest.fixture()
def _isolate_create_project_kb(tmp_path, monkeypatch):
    """Redirect the Create-Project flow's KB writes to an ISOLATED tmp KB.

    ``POST /api/v1/projects`` has bootstrap side-effects (the ``init.sh``
    subprocess, the :class:`KnowledgeBaseWriter`) that otherwise land dirs
    under the SHARED ``/home/icc/knowledge/projects/<slug>/`` — the ghost
    scaffold dirs cleaned by hand 2026-06-13 + 2026-07-09. Isolation, not
    clean-up: point the KB root at ``tmp_path`` so nothing touches the real KB
    even on a mid-test crash, and force ``init.sh`` into ``dry_run`` so its
    subprocess performs no ``/opt/projects`` or KB filesystem writes regardless
    of whether ``template_init_script_path`` is configured in this environment.

    Neutralises all three ghost vectors:
      1. ``settings.knowledge_base_path`` → tmp (``get_knowledge_base_writer``
         reads it at call time).
      2. ``get_knowledge_base_writer`` DI on the shared app → a tmp-rooted
         writer (belt-and-suspenders; modules that mount the router on their
         OWN app also override this on that app).
      3. ``invoke_init_script`` → dry-run (the historical ghost vector).

    Doubles as a live regression sentinel: snapshots the real KB ``projects``
    dir before the test and asserts NO new dir appeared there afterwards (the
    exact ghost-dir check the fix targets).
    """
    # Capture the REAL KB projects dir BEFORE we monkeypatch settings.
    real_kb_projects = Path(settings.knowledge_base_path) / "projects"
    before = {p.name for p in real_kb_projects.iterdir()} if real_kb_projects.is_dir() else set()

    kb_root = tmp_path / "knowledge"
    (kb_root / "projects").mkdir(parents=True)

    # (1) Settings-rooted KB access (``get_knowledge_base_writer`` reads this at
    #     call time) + (2) belt-and-suspenders DI override of the writer itself.
    monkeypatch.setattr(settings, "knowledge_base_path", str(kb_root))
    app.dependency_overrides[get_knowledge_base_writer] = lambda: KnowledgeBaseWriter(kb_root)

    # (3) init.sh — the historical ghost vector. Force dry-run so the subprocess
    #     never writes to /opt/projects or the KB even if the init script path is
    #     configured. Patched on the route module because it imports
    #     ``invoke_init_script`` by value (binding-by-value), so patching the
    #     source module would not rebind the route's reference.
    real_invoke = template_bootstrap.invoke_init_script

    def _dry_run_invoke(db, project, **kwargs):
        kwargs.setdefault("dry_run", True)
        return real_invoke(db, project, **kwargs)

    monkeypatch.setattr("backend.api.routes.projects.invoke_init_script", _dry_run_invoke)

    yield kb_root

    app.dependency_overrides.pop(get_knowledge_base_writer, None)

    after = {p.name for p in real_kb_projects.iterdir()} if real_kb_projects.is_dir() else set()
    new_dirs = after - before
    assert not new_dirs, (
        f"Create-Project test polluted the real KB {real_kb_projects}: {sorted(new_dirs)} — "
        "KB isolation broke (docs/specs/kb-ghost-root-cause.md Fix 1 / kb-ghost-followup.md Fix A)."
    )
