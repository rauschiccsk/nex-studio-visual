"""Migration runner — pre-flight guards, orchestration, and the run report.

This module owns the THREE pre-flight guards (two distinct DB-name guards + one
full referenced-user existence check) and the per-project orchestration loop with
its dry-run/apply switch. It is the only module that opens the two engines.

The guards are deliberately fail-closed and run BEFORE any per-project write
transaction opens, so a mis-pointed run (source==target, target==PROD, or a target
missing a referenced user) aborts with the target completely untouched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import Connection

from backend.config.settings import settings
from backend.db.base import Base
from backend.db.models.foundation import User
from backend.db.session import _ensure_pg8000_driver
from backend.services.migration.config import DEFAULT_REPORT_DIR, MigrationConfig
from backend.services.migration.copier import advance_deploy_seq, copy_project
from backend.services.migration.verify import CRITICAL, capture_pipeline_counts, verify_project

# The pure DB-name helpers are the SINGLE source of truth for the isolation guard
# (also used by the test suite's conftest). Reusing them keeps one implementation.
from tests._db_guard import assert_test_db_distinct, database_name

# The source tables the tool reflects + reads (SELECT-only). feats/tasks are reached
# under their epic/feat; pipeline_state/pipeline_message are NEVER read (OQ-6).
SOURCE_TABLE_NAMES: tuple[str, ...] = (
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
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MigrationGuardError(RuntimeError):
    """A pre-flight guard refused the run. The target was never written."""


class MigrationPreflightError(MigrationGuardError):
    """The target is missing one or more referenced users (fail-closed, B2)."""

    def __init__(self, missing_user_ids: list[str]) -> None:
        self.missing_user_ids = missing_user_ids
        super().__init__(
            "Referenced users missing in target — sync users first. Missing ids: " + ", ".join(missing_user_ids)
        )


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


@dataclass
class ProjectResult:
    """Outcome for one source project."""

    slug: str
    #: 'migrated' | 'dry_run' | 'skipped' | 'failed'
    status: str
    reason: str | None = None
    #: per-table copied row counts (e.g. {'versions': 2, 'epics': 3, ...}).
    counts: dict[str, int] = field(default_factory=dict)
    #: verify findings — dicts with table/severity/code/detail only (NEVER content).
    findings: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "slug": self.slug,
            "status": self.status,
            "reason": self.reason,
            "counts": self.counts,
            "findings": self.findings,
        }


@dataclass
class MigrationReport:
    """The full run report. Serialisable to JSON — NEVER carries secret values (§4)."""

    dry_run: bool
    source_db: str
    target_db: str
    projects: list[ProjectResult] = field(default_factory=list)
    generated_at: str = ""
    #: Where the JSON report was written (None if the write was skipped).
    report_path: str | None = None

    @property
    def overall(self) -> str:
        """'ok' (all migrated/dry_run/skipped), 'partial' (some failed), 'fail' (all failed)."""
        if not self.projects:
            return "ok"
        failed = [p for p in self.projects if p.status == "failed"]
        if not failed:
            return "ok"
        if len(failed) == len(self.projects):
            return "fail"
        return "partial"

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "partial": 1, "fail": 2}[self.overall]

    def to_json(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "source_db": self.source_db,
            "target_db": self.target_db,
            "overall": self.overall,
            "generated_at": self.generated_at,
            "report_path": self.report_path,
            "projects": [p.to_json() for p in self.projects],
        }


# ---------------------------------------------------------------------------
# Engines + reflection
# ---------------------------------------------------------------------------


def make_engine(url: str):
    """Create a plain engine for ``url`` (pg8000 driver enforced)."""
    return create_engine(_ensure_pg8000_driver(url), pool_pre_ping=True)


def reflect_source_tables(conn: Connection, names: tuple[str, ...] = SOURCE_TABLE_NAMES) -> dict[str, Table]:
    """Reflect the named source tables (columns only — ``resolve_fks=False``).

    Each table is reflected into its OWN ``MetaData`` with FK resolution disabled,
    so v1-only FK targets (project_modules, users) are not pulled in and no
    "unresolved foreign key" noise is emitted. The tool only ever reads columns.
    """
    tables: dict[str, Table] = {}
    for name in names:
        tables[name] = Table(name, MetaData(), autoload_with=conn, resolve_fks=False)
    return tables


# ---------------------------------------------------------------------------
# Guard 1 + 2: two distinct DB-name guards (m4)
# ---------------------------------------------------------------------------


def assert_source_target_distinct(source_url: str, target_url: str) -> None:
    """Refuse if the source and target are the SAME database (guard 1, m4).

    Reuses the isolation helper: source must never equal target, else the tool
    would copy a DB onto itself.
    """
    assert_test_db_distinct(source_url, target_url)


def assert_target_not_prod(target_url: str, allow_prod_target: bool) -> None:
    """Refuse a target whose DB name equals the cockpit PROD DB name (guard 2, m4).

    A SEPARATE check from :func:`assert_source_target_distinct`: it compares the
    target DB name against ``settings.database_url`` (the running cockpit's own DB).
    In build/CI the target is always ``nexstudio_test`` → this passes. Refuses unless
    the operator passes ``--i-understand-target-is-prod``.
    """
    if allow_prod_target:
        return
    prod_name = database_name(settings.database_url)
    target_name = database_name(target_url)
    if target_name == prod_name:
        raise MigrationGuardError(
            f"Refusing to migrate INTO the cockpit PROD database ({target_name!r}). "
            "Pass --i-understand-target-is-prod to override (a deliberate release action)."
        )


# ---------------------------------------------------------------------------
# Guard 3: FULL referenced-user existence (B2)
# ---------------------------------------------------------------------------


def collect_referenced_user_ids(
    source_conn: Connection,
    source_tables: dict[str, Table],
    project_ids: list[UUID],
) -> set[UUID]:
    """Collect every ``users.id`` referenced by the to-be-copied rows (B2).

    The FULL set across all five referencing columns on copied tables:
    projects.created_by, projects.owner_id, bugs.created_by,
    deploy_events.actor_id, project_members.user_id. ``owner_id`` is read only if
    the v1 source actually has that column (it is a v2-added column that some v1
    snapshots carry via CR-NS-012). NULLs are ignored (SET NULL columns).
    """
    if not project_ids:
        return set()

    referenced: set[UUID] = set()

    projects = source_tables["projects"]
    proj_cols = [projects.c.created_by]
    if "owner_id" in projects.c:
        proj_cols.append(projects.c.owner_id)
    for row in source_conn.execute(select(*proj_cols).where(projects.c.id.in_(project_ids))):
        for value in row:
            if value is not None:
                referenced.add(value)

    bugs = source_tables["bugs"]
    for (created_by,) in source_conn.execute(select(bugs.c.created_by).where(bugs.c.project_id.in_(project_ids))):
        if created_by is not None:
            referenced.add(created_by)

    deploy_events = source_tables["deploy_events"]
    for (actor_id,) in source_conn.execute(
        select(deploy_events.c.actor_id).where(deploy_events.c.project_id.in_(project_ids))
    ):
        if actor_id is not None:
            referenced.add(actor_id)

    members = source_tables["project_members"]
    for (user_id,) in source_conn.execute(select(members.c.user_id).where(members.c.project_id.in_(project_ids))):
        if user_id is not None:
            referenced.add(user_id)

    return referenced


def existing_user_ids(target_conn: Connection, candidate_ids: set[UUID]) -> set[UUID]:
    """Return the subset of ``candidate_ids`` that EXIST in the target users table."""
    if not candidate_ids:
        return set()
    rows = target_conn.execute(select(User.id).where(User.id.in_(list(candidate_ids))))
    return {row[0] for row in rows}


def find_missing_users(referenced: set[UUID], existing: set[UUID]) -> set[UUID]:
    """Pure set difference — referenced users that are NOT present in target."""
    return set(referenced) - set(existing)


def assert_referenced_users_exist(
    source_conn: Connection,
    source_tables: dict[str, Table],
    target_conn: Connection,
    project_ids: list[UUID],
) -> None:
    """Fail-closed if the target is missing any referenced user (guard 3, B2).

    Runs BEFORE any per-project write transaction. Missing users abort the whole
    run with the target untouched, listing the missing ids ("sync users first").
    """
    referenced = collect_referenced_user_ids(source_conn, source_tables, project_ids)
    existing = existing_user_ids(target_conn, referenced)
    missing = find_missing_users(referenced, existing)
    if missing:
        raise MigrationPreflightError(sorted(str(m) for m in missing))


# ---------------------------------------------------------------------------
# Report path helper
# ---------------------------------------------------------------------------


def _default_report_path() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(DEFAULT_REPORT_DIR, f"{ts}.json")


def write_report(config: MigrationConfig, report: MigrationReport) -> str | None:
    """Write the JSON report to ``report_path`` (best-effort). Returns the path.

    NEVER writes a secret value — the report carries only slugs, counts, and
    finding metadata (table/severity/code/detail/file_path). If the target
    directory is not writable (e.g. in CI), the write is skipped and None returned.
    """
    path = config.report_path or _default_report_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report.to_json(), fh, indent=2, default=str)
        return path
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _select_projects(source_conn: Connection, projects_table: Table, config: MigrationConfig) -> list[dict]:
    """Select the source projects to migrate, applying the slug/status filters."""
    stmt = select(projects_table)
    if config.only_slugs:
        stmt = stmt.where(projects_table.c.slug.in_(list(config.only_slugs)))
    stmt = stmt.order_by(projects_table.c.slug)
    rows = [dict(m) for m in source_conn.execute(stmt).mappings().all()]
    if config.exclude_statuses:
        rows = [r for r in rows if r.get("status") not in config.exclude_statuses]
    return rows


def _target_slug_exists(target_conn: Connection, slug: str) -> bool:
    projects = Base.metadata.tables["projects"]
    return target_conn.execute(select(projects.c.id).where(projects.c.slug == slug)).first() is not None


def _migrate_one_project(
    source_conn: Connection,
    source_tables: dict[str, Table],
    target_engine,
    project_row: dict,
    config: MigrationConfig,
) -> ProjectResult:
    """Copy + verify ONE project in its own target transaction (M4 isolation).

    Skip-if-slug-exists (M5, idempotent), then copy → verify. A CRITICAL finding or
    any error rolls THIS project back and records a failure; other projects continue.
    Dry-run rolls back after a clean verify; apply commits.
    """
    slug = project_row["slug"]
    conn = target_engine.connect()
    trans = conn.begin()
    try:
        if _target_slug_exists(conn, slug):
            trans.rollback()
            return ProjectResult(slug=slug, status="skipped", reason="slug already exists in target (M5)")

        pipeline_baseline = capture_pipeline_counts(conn)
        counts = copy_project(source_conn, source_tables, conn, project_row, config)
        findings = verify_project(source_conn, source_tables, conn, project_row, pipeline_baseline)
        criticals = [f for f in findings if f["severity"] == CRITICAL]

        if criticals:
            trans.rollback()
            return ProjectResult(
                slug=slug, status="failed", reason="verification CRITICAL", counts=counts, findings=findings
            )
        if config.dry_run:
            trans.rollback()
            return ProjectResult(
                slug=slug, status="dry_run", reason="rolled back (dry-run)", counts=counts, findings=findings
            )
        trans.commit()
        return ProjectResult(slug=slug, status="migrated", counts=counts, findings=findings)
    except Exception as exc:  # noqa: BLE001 — per-project isolation: record + continue
        trans.rollback()
        return ProjectResult(slug=slug, status="failed", reason=f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()


def run_migration(config: MigrationConfig) -> MigrationReport:
    """Run the whole migration: 3 pre-flight guards, per-project copy/verify, report.

    Guards (fail-closed, BEFORE any write): source!=target, target!=cockpit-PROD,
    and the FULL referenced-user existence check. Then each project is migrated in
    its own transaction. In --apply the deploy_events IDENTITY high-water-mark is
    advanced ONCE after the loop (M3). Returns a :class:`MigrationReport`; a
    pre-flight failure raises (target untouched).
    """
    assert_source_target_distinct(config.source_url, config.target_url)
    assert_target_not_prod(config.target_url, config.allow_prod_target)

    source_engine = make_engine(config.source_url)
    target_engine = make_engine(config.target_url)
    report = MigrationReport(
        dry_run=config.dry_run,
        source_db=database_name(config.source_url),
        target_db=database_name(config.target_url),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        with source_engine.connect() as source_conn:
            source_tables = reflect_source_tables(source_conn)
            projects = _select_projects(source_conn, source_tables["projects"], config)
            project_ids = [p["id"] for p in projects]

            # Guard 3 (fail-closed) — BEFORE opening any per-project write transaction.
            with target_engine.connect() as check_conn:
                assert_referenced_users_exist(source_conn, source_tables, check_conn, project_ids)

            for project_row in projects:
                report.projects.append(
                    _migrate_one_project(source_conn, source_tables, target_engine, project_row, config)
                )

            # M3: advance the deploy_events IDENTITY once, after the apply loop.
            if not config.dry_run and any(p.status == "migrated" for p in report.projects):
                with target_engine.begin() as seq_conn:
                    advance_deploy_seq(seq_conn)
    finally:
        source_engine.dispose()
        target_engine.dispose()

    report.report_path = write_report(config, report)
    return report
