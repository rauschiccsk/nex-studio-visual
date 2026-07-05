"""Synthetic v1-shaped source builder + two-DB lifecycle helpers for STEP 8 e2e.

The migration tool needs TWO distinct databases; the SAVEPOINT conftest gives one.
So the e2e/guard tests stand up throwaway databases on the SAME test server
(:9178) — a v1-SHAPED source (built here via raw DDL, so it genuinely has
``projects.category`` + ``epics.module_id`` and lacks the v2-added columns) and a
v2-head target (via the real alembic chain). Both DB names are distinct from each
other and from the cockpit PROD name, so the tool's guards pass. Everything here is
PURELY SYNTHETIC — no real project data, never the real v1 source, never PROD.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, insert, text
from sqlalchemy.engine import Engine

from backend.db.base import Base
from backend.db.session import _ensure_pg8000_driver

# ---------------------------------------------------------------------------
# Fixed synthetic user ids — the tool references but does NOT copy users, so the
# target must be pre-seeded with these exact ids (matching the source refs).
# ---------------------------------------------------------------------------
U_CREATOR = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
U_OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
U_BUG = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
U_ACTOR = uuid.UUID("00000000-0000-0000-0000-0000000000c4")
U_MEMBER = uuid.UUID("00000000-0000-0000-0000-0000000000c5")

ALL_USER_IDS: dict[str, uuid.UUID] = {
    "created_by": U_CREATOR,
    "owner_id": U_OWNER,
    "bugs.created_by": U_BUG,
    "deploy_events.actor_id": U_ACTOR,
    "project_members.user_id": U_MEMBER,
}

# Source project slugs.
ALPHA = "alpha"  # multimodule, full valid tree, credential file PRESENT
BETA = "beta"  # singlemodule, owner_id NULL (fallback), credential file MISSING → WARN
GAMMA = "gamma"  # web, crafted to FAIL per-project (invalid bug.severity)

_BASE_TS = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)


def _ts(offset_minutes: int) -> datetime:
    """A distinct tz-aware timestamp — lets the e2e prove created_at/updated_at verbatim."""
    return _BASE_TS + timedelta(minutes=offset_minutes)


# ---------------------------------------------------------------------------
# v1-shaped DDL (no FK/CHECK constraints — a read-only source fixture; keeping it
# constraint-free lets us seed v1 values the v2 schema would reject: category,
# module_id, v1 pipeline enums, and a deliberately-invalid bug severity for the
# per-project isolation test).
# ---------------------------------------------------------------------------
_V1_DDL: tuple[str, ...] = (
    """
    CREATE TABLE projects (
        id UUID PRIMARY KEY, name TEXT, slug TEXT, category TEXT, description TEXT,
        status TEXT, backend_port INTEGER, frontend_port INTEGER, db_port INTEGER,
        repo_url TEXT, source_path TEXT, kb_path TEXT, owner_id UUID, created_by UUID,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE project_members (
        id UUID PRIMARY KEY, project_id UUID, user_id UUID, role TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE credentials (
        id UUID PRIMARY KEY, title TEXT, file_path TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE customers (
        id UUID PRIMARY KEY, project_id UUID, name TEXT, slug TEXT, subdomain TEXT,
        integrations JSONB, credential_id UUID, notes TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE versions (
        id UUID PRIMARY KEY, project_id UUID, version_number TEXT, name TEXT, status TEXT,
        description TEXT, target_date DATE, release_date DATE,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE epics (
        id UUID PRIMARY KEY, project_id UUID, version_id UUID, module_id UUID,
        number INTEGER, title TEXT, status TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE feats (
        id UUID PRIMARY KEY, epic_id UUID, number INTEGER, title TEXT, description TEXT,
        status TEXT, estimated_minutes INTEGER, actual_minutes INTEGER,
        task_count INTEGER, auto_fix_count INTEGER,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE tasks (
        id UUID PRIMARY KEY, feat_id UUID, number INTEGER, title TEXT, description TEXT,
        task_type TEXT, status TEXT, priority TEXT, estimated_minutes INTEGER,
        actual_minutes INTEGER, checklist_type TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE bugs (
        id UUID PRIMARY KEY, project_id UUID, version_id UUID, bug_number INTEGER,
        title TEXT, description TEXT, severity TEXT, status TEXT, source TEXT,
        reported_by TEXT, environment TEXT, resolved_at TIMESTAMPTZ, commit_hash TEXT,
        created_by UUID, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE backlog_items (
        id UUID PRIMARY KEY, project_id UUID, number INTEGER, title TEXT, description TEXT,
        priority TEXT, status TEXT, version_id UUID, realized_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE deploy_events (
        id UUID PRIMARY KEY, seq BIGINT, customer_id UUID, project_id UUID,
        version_number TEXT, environment TEXT, event_type TEXT, status TEXT,
        actor_id UUID, detail TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE pipeline_state (
        id UUID PRIMARY KEY, version_id UUID, flow_type TEXT, current_stage TEXT,
        current_actor TEXT, status TEXT, next_action TEXT,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """,
)

_V1_TABLE_NAMES: tuple[str, ...] = (
    "projects",
    "project_members",
    "credentials",
    "customers",
    "versions",
    "epics",
    "feats",
    "tasks",
    "bugs",
    "backlog_items",
    "deploy_events",
    "pipeline_state",
)


@dataclass
class SourceSeed:
    """Handles to the seeded source for the e2e assertions."""

    cred_present_path: str
    cred_missing_path: str
    cred_present_id: uuid.UUID
    cred_missing_id: uuid.UUID


# ---------------------------------------------------------------------------
# Schema + seeding
# ---------------------------------------------------------------------------


def create_v1_schema(engine: Engine) -> None:
    """(Re)create the v1-shaped source schema (drop+create for a clean throwaway DB)."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        for ddl in _V1_DDL:
            conn.execute(text(ddl))


def _reflect(engine: Engine) -> dict[str, Table]:
    md = MetaData()
    return {name: Table(name, md, autoload_with=engine, resolve_fks=False) for name in _V1_TABLE_NAMES}


def seed_source(engine: Engine, cred_present_path: str, cred_missing_path: str) -> SourceSeed:
    """Seed the synthetic v1 source (alpha/beta/gamma). Returns the credential handles."""
    t = _reflect(engine)
    cred_present_id = uuid.uuid4()
    cred_missing_id = uuid.uuid4()

    def ins(table_key: str, **values) -> None:
        with engine.begin() as conn:
            conn.execute(insert(t[table_key]).values(**values))

    # --- credentials (flat registry) ---
    ins(
        "credentials",
        id=cred_present_id,
        title="Alpha secret",
        file_path=cred_present_path,
        created_at=_ts(0),
        updated_at=_ts(1),
    )
    ins(
        "credentials",
        id=cred_missing_id,
        title="Beta secret",
        file_path=cred_missing_path,
        created_at=_ts(0),
        updated_at=_ts(1),
    )

    _seed_alpha(ins, cred_present_id)
    _seed_beta(ins, cred_missing_id)
    _seed_gamma(ins)

    return SourceSeed(
        cred_present_path=cred_present_path,
        cred_missing_path=cred_missing_path,
        cred_present_id=cred_present_id,
        cred_missing_id=cred_missing_id,
    )


def _seed_alpha(ins, cred_present_id: uuid.UUID) -> None:
    """alpha: multimodule, explicit owner, 2 versions, epics WITH module_id, full tree."""
    pid = uuid.uuid4()
    ins(
        "projects",
        id=pid,
        name="Alpha",
        slug=ALPHA,
        category="multimodule",
        description="alpha desc",
        status="active",
        backend_port=13001,
        frontend_port=13002,
        db_port=13003,
        repo_url="https://example/alpha",
        source_path="/opt/projects/alpha",
        kb_path="projects/alpha",
        owner_id=U_OWNER,
        created_by=U_CREATOR,
        created_at=_ts(10),
        updated_at=_ts(11),
    )

    v1 = uuid.uuid4()
    v2 = uuid.uuid4()
    ins(
        "versions",
        id=v1,
        project_id=pid,
        version_number="v1.0.0",
        name="First",
        status="released",
        description="v1",
        created_at=_ts(12),
        updated_at=_ts(13),
    )
    ins(
        "versions",
        id=v2,
        project_id=pid,
        version_number="v1.1.0",
        name="Second",
        status="active",
        description="v11",
        created_at=_ts(14),
        updated_at=_ts(15),
    )

    e1 = uuid.uuid4()
    e2 = uuid.uuid4()
    # module_id present here — the copier MUST ignore it (dropped in v2).
    ins(
        "epics",
        id=e1,
        project_id=pid,
        version_id=v1,
        module_id=uuid.uuid4(),
        number=1,
        title="Epic 1",
        status="done",
        created_at=_ts(16),
        updated_at=_ts(17),
    )
    ins(
        "epics",
        id=e2,
        project_id=pid,
        version_id=v1,
        module_id=uuid.uuid4(),
        number=2,
        title="Epic 2",
        status="planned",
        created_at=_ts(18),
        updated_at=_ts(19),
    )

    f1 = uuid.uuid4()
    f2 = uuid.uuid4()
    ins(
        "feats",
        id=f1,
        epic_id=e1,
        number=1,
        title="Feat 1",
        description="f1 desc",
        status="done",
        estimated_minutes=60,
        actual_minutes=55,
        task_count=2,
        auto_fix_count=1,
        created_at=_ts(20),
        updated_at=_ts(21),
    )
    ins(
        "feats",
        id=f2,
        epic_id=e1,
        number=2,
        title="Feat 2",
        description="f2 desc",
        status="todo",
        estimated_minutes=30,
        actual_minutes=None,
        task_count=0,
        auto_fix_count=0,
        created_at=_ts(22),
        updated_at=_ts(23),
    )

    ins(
        "tasks",
        id=uuid.uuid4(),
        feat_id=f1,
        number=1,
        title="Task 1",
        description="t1",
        task_type="backend",
        status="done",
        priority="high",
        estimated_minutes=20,
        actual_minutes=18,
        checklist_type="be",
        created_at=_ts(24),
        updated_at=_ts(25),
    )
    ins(
        "tasks",
        id=uuid.uuid4(),
        feat_id=f1,
        number=2,
        title="Task 2",
        description="t2",
        task_type="frontend",
        status="todo",
        priority="normal",
        estimated_minutes=None,
        actual_minutes=None,
        checklist_type=None,
        created_at=_ts(26),
        updated_at=_ts(27),
    )

    ins(
        "bugs",
        id=uuid.uuid4(),
        project_id=pid,
        version_id=v1,
        bug_number=1,
        title="Bug 1",
        description="b1",
        severity="major",
        status="resolved",
        source="internal",
        reported_by="qa",
        environment="uat",
        resolved_at=_ts(30),
        commit_hash="abc123",
        created_by=U_BUG,
        created_at=_ts(28),
        updated_at=_ts(29),
    )

    cust = uuid.uuid4()
    ins(
        "customers",
        id=cust,
        project_id=pid,
        name="Alpha Cust",
        slug="acme",
        subdomain="acme",
        integrations={"erp": "x"},
        credential_id=cred_present_id,
        notes="note",
        created_at=_ts(31),
        updated_at=_ts(32),
    )

    ins(
        "deploy_events",
        id=uuid.uuid4(),
        seq=100,
        customer_id=cust,
        project_id=pid,
        version_number="v1.0.0",
        environment="prod",
        event_type="deploy",
        status="ok",
        actor_id=U_ACTOR,
        detail="deployed",
        created_at=_ts(33),
        updated_at=_ts(34),
    )
    ins(
        "deploy_events",
        id=uuid.uuid4(),
        seq=101,
        customer_id=cust,
        project_id=pid,
        version_number="v1.0.0",
        environment="prod",
        event_type="accept",
        status="ok",
        actor_id=U_ACTOR,
        detail="accepted",
        created_at=_ts(35),
        updated_at=_ts(36),
    )

    ins(
        "backlog_items",
        id=uuid.uuid4(),
        project_id=pid,
        number=1,
        title="REQ 1",
        description="future",
        priority="high",
        status="open",
        version_id=None,
        realized_at=None,
        created_at=_ts(37),
        updated_at=_ts(38),
    )

    ins(
        "project_members",
        id=uuid.uuid4(),
        project_id=pid,
        user_id=U_MEMBER,
        role="member",
        created_at=_ts(39),
        updated_at=_ts(40),
    )

    # A v1-enum pipeline_state row — NOT copied (OQ-6). Its v1 stage/actor/flow values
    # would be rejected by the v2 CHECKs, proving the tool must skip it.
    ins(
        "pipeline_state",
        id=uuid.uuid4(),
        version_id=v1,
        flow_type="cr",
        current_stage="gate_a",
        current_actor="designer",
        status="awaiting_director",
        next_action="review",
        created_at=_ts(41),
        updated_at=_ts(42),
    )


def _seed_beta(ins, cred_missing_id: uuid.UUID) -> None:
    """beta: singlemodule, owner_id NULL (fallback→creator), credential file MISSING → WARN."""
    pid = uuid.uuid4()
    ins(
        "projects",
        id=pid,
        name="Beta",
        slug=BETA,
        category="singlemodule",
        description="beta desc",
        status="active",
        backend_port=None,
        frontend_port=None,
        db_port=None,
        repo_url=None,
        source_path="/opt/projects/beta",
        kb_path=None,
        owner_id=None,
        created_by=U_CREATOR,
        created_at=_ts(50),
        updated_at=_ts(51),
    )

    v = uuid.uuid4()
    ins(
        "versions",
        id=v,
        project_id=pid,
        version_number="v0.1.0",
        name=None,
        status="active",
        description=None,
        created_at=_ts(52),
        updated_at=_ts(53),
    )

    e = uuid.uuid4()
    ins(
        "epics",
        id=e,
        project_id=pid,
        version_id=v,
        module_id=None,
        number=1,
        title="B Epic",
        status="planned",
        created_at=_ts(54),
        updated_at=_ts(55),
    )
    f = uuid.uuid4()
    ins(
        "feats",
        id=f,
        epic_id=e,
        number=1,
        title="B Feat",
        description="",
        status="todo",
        estimated_minutes=None,
        actual_minutes=None,
        task_count=0,
        auto_fix_count=0,
        created_at=_ts(56),
        updated_at=_ts(57),
    )
    ins(
        "tasks",
        id=uuid.uuid4(),
        feat_id=f,
        number=1,
        title="B Task",
        description="",
        task_type="docs",
        status="todo",
        priority="normal",
        estimated_minutes=None,
        actual_minutes=None,
        checklist_type=None,
        created_at=_ts(58),
        updated_at=_ts(59),
    )

    cust = uuid.uuid4()
    ins(
        "customers",
        id=cust,
        project_id=pid,
        name="Beta Cust",
        slug="bcorp",
        subdomain=None,
        integrations=None,
        credential_id=cred_missing_id,
        notes=None,
        created_at=_ts(60),
        updated_at=_ts(61),
    )
    ins(
        "deploy_events",
        id=uuid.uuid4(),
        seq=50,
        customer_id=cust,
        project_id=pid,
        version_number="v0.1.0",
        environment="uat",
        event_type="deploy",
        status="ok",
        actor_id=U_ACTOR,
        detail="uat",
        created_at=_ts(62),
        updated_at=_ts(63),
    )
    ins(
        "project_members",
        id=uuid.uuid4(),
        project_id=pid,
        user_id=U_MEMBER,
        role="member",
        created_at=_ts(64),
        updated_at=_ts(65),
    )


def _seed_gamma(ins) -> None:
    """gamma: crafted to FAIL per-project — bug.severity='blocker' violates the v2 CHECK."""
    pid = uuid.uuid4()
    ins(
        "projects",
        id=pid,
        name="Gamma",
        slug=GAMMA,
        category="web",
        description="gamma",
        status="active",
        backend_port=None,
        frontend_port=None,
        db_port=None,
        repo_url=None,
        source_path="/opt/projects/gamma",
        kb_path=None,
        owner_id=None,
        created_by=U_CREATOR,
        created_at=_ts(70),
        updated_at=_ts(71),
    )
    v = uuid.uuid4()
    ins(
        "versions",
        id=v,
        project_id=pid,
        version_number="v0.1.0",
        name=None,
        status="active",
        description=None,
        created_at=_ts(72),
        updated_at=_ts(73),
    )
    # Invalid severity — the v2 ck_bugs_severity CHECK rejects this at INSERT, so gamma's
    # transaction rolls back while alpha/beta commit (per-project isolation, M4).
    ins(
        "bugs",
        id=uuid.uuid4(),
        project_id=pid,
        version_id=v,
        bug_number=1,
        title="G Bug",
        description="bad",
        severity="blocker",
        status="new",
        source="internal",
        reported_by=None,
        environment=None,
        resolved_at=None,
        commit_hash=None,
        created_by=U_BUG,
        created_at=_ts(74),
        updated_at=_ts(75),
    )


# ---------------------------------------------------------------------------
# Two-DB lifecycle helpers (create/drop throwaway DBs, bring target to v2 head)
# ---------------------------------------------------------------------------


def _admin_url(url: str) -> str:
    base, _ = url.rsplit("/", 1)
    return base + "/postgres"


def create_database(url: str) -> None:
    """Create the database named in ``url`` (dropping any prior instance first)."""
    drop_database(url)
    name = url.rsplit("/", 1)[-1].split("?")[0]
    admin = create_engine(_ensure_pg8000_driver(_admin_url(url)), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))  # noqa: S608 — controlled test DB name
    admin.dispose()


def drop_database(url: str) -> None:
    """Drop the database named in ``url`` (terminating any lingering connections)."""
    name = url.rsplit("/", 1)[-1].split("?")[0]
    admin = create_engine(_ensure_pg8000_driver(_admin_url(url)), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))  # noqa: S608 — controlled test DB name
    admin.dispose()


def bring_to_v2_head(url: str) -> None:
    """Reset the target DB to the v2 migration head (drop schema + alembic upgrade)."""
    engine = create_engine(_ensure_pg8000_driver(url), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    repo_root = Path(__file__).resolve().parents[3]
    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    alembic_cfg.cmd_opts = type("opts", (), {"x": [f"url={_ensure_pg8000_driver(url)}"]})()
    command.upgrade(alembic_cfg, "head")

    # Purge migration-seeded baseline data (admin user + session) — each test seeds
    # its own users; a leftover admin would collide.
    engine = create_engine(_ensure_pg8000_driver(url), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM user_sessions"))
        conn.execute(text("DELETE FROM users"))
    engine.dispose()


_TARGET_TRUNCATE_TABLES = (
    "user_sessions",
    "users",
    "projects",
    "project_members",
    "credentials",
    "customers",
    "versions",
    "epics",
    "feats",
    "tasks",
    "bugs",
    "backlog_items",
    "deploy_events",
    "pipeline_state",
    "pipeline_message",
)


def truncate_target(engine: Engine) -> None:
    """Wipe all app data in the target (RESTART IDENTITY resets deploy_events.seq)."""
    tables = ", ".join(_TARGET_TRUNCATE_TABLES)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))  # noqa: S608 — fixed table list


def seed_target_users(engine: Engine, user_ids: dict[str, uuid.UUID] | None = None) -> None:
    """Seed the v2 target users the source references (by fixed UUID)."""
    ids = {v for v in (user_ids or ALL_USER_IDS).values()}
    users = Base.metadata.tables["users"]
    rows = [
        {
            "id": uid,
            "username": f"user_{str(uid)[-4:]}",
            "email": f"{str(uid)[-8:]}@example.com",
            "password_hash": "x",
            "role": "ri",
        }
        for uid in ids
    ]
    with engine.begin() as conn:
        conn.execute(insert(users), rows)
