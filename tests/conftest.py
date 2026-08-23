"""Pytest configuration with SAVEPOINT transaction isolation.

Uses TEST_DATABASE_URL — NEVER the production DATABASE_URL.
Each test runs in a savepoint that is rolled back after the test,
keeping the test database clean without costly create/drop cycles.

ONE DATABASE PER RUN (audit 2026-08-23, finding 6). The base name comes from ``TEST_DATABASE_URL`` /
``settings.test_database_url``; ``_get_test_database_url`` then suffixes it with the running
process (xdist worker id, else PID). Before this, two pytest runs in the same checkout — two agents
working the same ticket, which is the normal case here — shared one database whose schema each of them
DROPs at startup, and the loser reported failures that did not exist. See ``tests._db_guard.run_scoped_url``
for the measurements. The per-run database is created at session start and DROPPED at session end.
"""

import os
import re
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
from tests._db_guard import assert_test_db_distinct, run_scoped_url

#: Suffix pattern of a run-scoped database (``…_test_gw0`` / ``…_test_p12345``). Used to sweep databases
#: left behind by a run that was killed before its teardown — a stray one is harmless but they accumulate.
_RUN_DB_SUFFIX = re.compile(r"_(?:gw\d+|p\d+)$")


def _run_token() -> str:
    """Identify THIS pytest process: the xdist worker id when sharded, else the PID."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    return worker if worker else f"p{os.getpid()}"


def _base_test_database_url() -> str:
    """The configured test database URL (shared name, no run suffix), ensuring pg8000 driver."""
    from backend.config.settings import settings

    url = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)
    return _ensure_pg8000_driver(url)


def _get_test_database_url() -> str:
    """The database THIS run owns: the configured URL, suffixed per process.

    Computed once and cached in the environment, so every caller in the run (the ``test_engine``
    fixture, the ``_guard_prod_db_isolation`` re-assertion, ``backend/tests`` which re-imports the
    fixture) agrees on one name — a second call must never mint a second database.
    """
    cached = os.environ.get("_NEXSTUDIO_RUN_TEST_DATABASE_URL")
    if cached:
        return cached
    url = run_scoped_url(_base_test_database_url(), _run_token())
    os.environ["_NEXSTUDIO_RUN_TEST_DATABASE_URL"] = url
    return url


def _drop_database(url: str) -> None:
    """Drop the database named by ``url`` (best effort — a leftover is junk, not a failure)."""
    head, _, tail = url.rpartition("/")
    db_name = tail.split("?")[0]
    admin_engine = create_engine(head + "/postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            # FORCE (PG 13+) evicts a connection some test left open; without it the DROP would block.
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    except Exception as exc:  # pragma: no cover - cleanup must never fail a green run
        print(f"[conftest] could not drop the run database {db_name!r}: {exc}")
    finally:
        admin_engine.dispose()


def _sweep_orphaned_run_databases(base_url: str) -> None:
    """Drop run databases whose pytest process is gone (a run killed before teardown).

    Only ``<base>_p<pid>`` names are considered, and only when no process with that PID is alive — an
    xdist worker database (``…_gw0``) carries no PID and is left alone, as is any database that does not
    look like ours. Best effort: this is housekeeping, never a gate.
    """
    head, _, tail = base_url.rpartition("/")
    base_name = tail.split("?")[0]
    admin_engine = create_engine(head + "/postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            names = [
                row
                for row in conn.execute(
                    text("SELECT datname FROM pg_database WHERE datname LIKE :pat"),
                    {"pat": f"{base_name}_p%"},
                ).scalars()
            ]
        for name in names:
            if not _RUN_DB_SUFFIX.search(name):
                continue
            pid = int(name.rsplit("_p", 1)[1])
            try:
                os.kill(pid, 0)
                continue  # that run is alive — its database is in use
            except PermissionError:
                continue  # alive, owned by another user
            except (OSError, ProcessLookupError):
                pass
            _drop_database(f"{head}/{name}")
    except Exception as exc:  # pragma: no cover - housekeeping must never fail a run
        print(f"[conftest] run-database sweep skipped: {exc}")
    finally:
        admin_engine.dispose()


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

    The database is PRIVATE TO THIS RUN (see the module docstring): created here, dropped on teardown.
    Two concurrent runs in the same checkout therefore cannot drop each other's schema mid-test, which
    is what made this gate report failures that did not exist (audit 2026-08-23, finding 6).
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

    # Only AFTER the distinctness guard: the sweep issues DROP DATABASE, so it must never run against a
    # configuration the guard would have rejected.
    _sweep_orphaned_run_databases(_base_test_database_url())

    _ensure_test_database_exists(url)

    # Reset the persistent test DB to the v2 migration head before any test
    # touches it. ``Base`` is imported above purely so every ORM model is
    # registered on import (collection-time guard); the schema itself comes
    # from the migration chain, not ``create_all``.
    _reset_test_schema_to_head(url)

    engine = create_engine(url, pool_pre_ping=True)

    yield engine

    engine.dispose()
    # The run owns this database and nothing outlives the run — drop it, so repeated runs do not leave a
    # trail of ``…_test_p<pid>`` databases behind. A failure here is logged, never raised: cleanup must
    # not turn a green suite red.
    _drop_database(url)


@pytest.fixture(scope="session", autouse=True)
def _isolate_projects_root(tmp_path_factory):
    """Redirect ``PROJECTS_ROOT`` to a throwaway temp dir for the WHOLE session, so create-project /
    charter-provisioning / metrics tests NEVER scaffold into the REAL ``/opt/projects`` — the source of the
    1690 ``p-<hex>`` / ``metrics-phase-<hex>`` junk dirs that polluted the dev workspace (Director 2026-07-10).

    ``PROJECTS_ROOT`` is a hardcoded module global in ``claude_agent`` (no env/settings knob). ``project_memory``
    copied it at import time, so BOTH bindings are rebound — a leftover copy at ``project_memory.PROJECTS_ROOT``
    would otherwise still point at the real dir. ``api.routes.projects`` re-imports it INSIDE its functions, so
    it picks up this rebind at call time. Per-test ``monkeypatch.setattr(<mod>, "PROJECTS_ROOT", ...)`` still
    overrides for a specific test — this only moves the DEFAULT off the real workspace.
    """
    from backend.services import claude_agent, project_memory

    tmp = tmp_path_factory.mktemp("projects_root")
    orig_ca, orig_pm = claude_agent.PROJECTS_ROOT, project_memory.PROJECTS_ROOT
    claude_agent.PROJECTS_ROOT = tmp
    project_memory.PROJECTS_ROOT = tmp
    yield tmp
    claude_agent.PROJECTS_ROOT = orig_ca
    project_memory.PROJECTS_ROOT = orig_pm


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


# ---------------------------------------------------------------------------
# Deterministic host port probe
#
# ``port_registry`` now resolves availability against the HOST (the Docker
# published-port map) as well as the ``projects`` table — without that, a port
# a neighbouring container is already serving on reads as free, which is how
# the cockpit handed out 10111 while ``nex-manager-frontend`` was publishing it.
#
# That makes the real machine an input to the test suite, which it must never
# be: ANDROS genuinely publishes 10111 / 10160-10162 / 10170-10173 and the
# suite genuinely uses 10111 / 10160 / 10161 / 10170, so an unstubbed probe
# would make port tests pass or fail depending on what happens to be running.
# The probe is therefore stubbed for EVERY test, defaulting to "host holds
# nothing"; a test that cares fills in ``host_ports``.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_system_setting_cache():
    """Clear the process-global typed-settings cache around every test.

    ``system_setting`` memoises ``get_str`` / ``get_int`` for 30s. The DB rows
    a test writes are rolled back with its SAVEPOINT, but that cache is NOT —
    so a test upserting e.g. ``reserved_port_ranges`` would leak its value into
    later tests for half a minute, and the port range / block size / reserved
    ranges are all read through those cached getters.
    """
    from backend.services import system_setting

    system_setting.invalidate_cache()
    yield
    system_setting.invalidate_cache()


@pytest.fixture()
def host_ports() -> dict[int, str]:
    """Fake ``{host_port: container_name}`` map the port registry sees.

    Empty by default, so a test that says nothing about the host keeps its
    original meaning ("free unless our own table says otherwise"). Request
    this fixture and populate it to simulate a neighbouring container::

        def test_x(host_ports, db_session):
            host_ports[10111] = "nex-manager-frontend"
            ...
    """
    return {}


@pytest.fixture(autouse=True)
def _deterministic_host_port_probe(monkeypatch, host_ports):
    """Pin the host port probe to :func:`host_ports` and drop its cache.

    Stubs the two host-facing primitives — the Docker map reader and the bind
    probe — rather than the public helpers, so all the logic under test
    (caching, fail-closed handling, the union in the allocators) really runs.
    A test that wants to exercise probe FAILURE monkeypatches
    ``_docker_published_ports`` itself to raise ``HostProbeError``.

    The module-level snapshot cache is cleared on the way in AND out: it is
    process-global, so a value cached by one test would otherwise leak into
    the next.
    """
    from backend.services import port_registry

    def _fake_docker_published_ports(timeout: float = port_registry.HOST_PROBE_TIMEOUT_SECONDS) -> dict[int, str]:
        return dict(host_ports)

    monkeypatch.setattr(port_registry, "_docker_published_ports", _fake_docker_published_ports)
    monkeypatch.setattr(port_registry, "_bind_probe_says_taken", lambda port: False)
    port_registry.invalidate_host_port_cache()
    yield
    port_registry.invalidate_host_port_cache()
