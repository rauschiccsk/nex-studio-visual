"""Per-project DB copier — the load-bearing testable core.

Reads one project's whole tree from the SOURCE (SELECT-only, via reflected tables
so a v1-shaped schema is read as-is), applies the pure transforms, and INSERTs it
into the TARGET preserving PK UUIDs + created_at/updated_at, in the CORRECTED FK
order:

    projects → project_members → credentials(subset via customers.credential_id)
    → customers → versions → epics → feats → tasks → bugs → backlog_items → deploy_events

Invariants:
* NEVER reads/writes ``epics.module_id`` (dropped in v2, migration 070).
* NEVER copies pipeline_state / pipeline_message / operational tables (OQ-6).
* Preserves ``deploy_events.seq`` verbatim; the IDENTITY high-water-mark is
  advanced ONCE after the apply loop via :func:`advance_deploy_seq` (M3).
* Target rows are written with SQLAlchemy Core ``insert`` against the v2 ORM
  ``Base.metadata`` tables, so explicit id/seq/timestamps override server
  defaults + the IDENTITY default.

The transaction boundary (per-project begin/commit-or-rollback) is owned by the
runner, not here — this function only issues the INSERTs on the connection given.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import Table, insert, select, text
from sqlalchemy.engine import Connection

from backend.db.base import Base
from backend.services.migration.config import MigrationConfig
from backend.services.migration.transforms import new_column_defaults, rewrite_source_path


def _target(name: str) -> Table:
    """The v2 ORM Core table for ``name`` (carries the real columns + defaults)."""
    return Base.metadata.tables[name]


def _select_by(conn: Connection, table: Table, **eq) -> list[Mapping]:
    """SELECT rows from a reflected source table filtered by equality columns."""
    stmt = select(table)
    for col, value in eq.items():
        stmt = stmt.where(table.c[col] == value)
    return list(conn.execute(stmt).mappings().all())


def _select_in(conn: Connection, table: Table, column: str, values: Sequence) -> list[Mapping]:
    """SELECT rows from a reflected source table where ``column`` is in ``values``."""
    if not values:
        return []
    stmt = select(table).where(table.c[column].in_(list(values)))
    return list(conn.execute(stmt).mappings().all())


def _insert_many(conn: Connection, name: str, rows: list[dict]) -> None:
    """Executemany INSERT into the target table (no-op for an empty list)."""
    if rows:
        conn.execute(insert(_target(name)), rows)


# ---------------------------------------------------------------------------
# Per-table row builders (source RowMapping → target column dict).
# ``.get`` tolerates a v1 source that lacks a v2-added column (returns None) and a
# v2-shaped source that has it (passes it through). Columns guaranteed in BOTH
# schemas are read directly.
# ---------------------------------------------------------------------------


def _project_dict(row: Mapping, config: MigrationConfig) -> dict:
    d = {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "status": row.get("status") or "active",
        "backend_port": row.get("backend_port"),
        "frontend_port": row.get("frontend_port"),
        "db_port": row.get("db_port"),
        "repo_url": row.get("repo_url"),
        "source_path": rewrite_source_path(row.get("source_path"), config.projects_root),
        "kb_path": row.get("kb_path"),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    # type / auth_mode / miera_autonomie / uat_slug / guardian_enabled /
    # custom_development_enabled / owner_id — the v2-added project columns.
    d.update(new_column_defaults(row))
    return d


def _member_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "role": row.get("role") or "member",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _credential_dict(row: Mapping) -> dict:
    # Registry pointer ONLY — the on-disk secret file is never opened (§4).
    return {
        "id": row["id"],
        "title": row["title"],
        "file_path": row["file_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _customer_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "slug": row["slug"],
        "subdomain": row.get("subdomain"),
        "integrations": row.get("integrations"),
        "credential_id": row.get("credential_id"),
        "notes": row.get("notes"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _version_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "version_number": row["version_number"],
        "name": row.get("name"),
        "status": row.get("status") or "planned",
        "description": row.get("description"),
        "target_date": row.get("target_date"),
        "release_date": row.get("release_date"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _epic_dict(row: Mapping) -> dict:
    # NEVER reads module_id (dropped v2). plain_description is v2-added → None from v1.
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "version_id": row.get("version_id"),
        "number": row["number"],
        "title": row["title"],
        "plain_description": row.get("plain_description"),
        "status": row.get("status") or "planned",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _feat_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "epic_id": row["epic_id"],
        "number": row["number"],
        "title": row["title"],
        "description": row.get("description") or "",
        "plain_description": row.get("plain_description"),
        "status": row.get("status") or "todo",
        "estimated_minutes": row.get("estimated_minutes"),
        "actual_minutes": row.get("actual_minutes"),
        "task_count": row.get("task_count") or 0,
        "auto_fix_count": row.get("auto_fix_count") or 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _task_dict(row: Mapping) -> dict:
    # baseline_sha + plain_description are v2-added → None from v1.
    return {
        "id": row["id"],
        "feat_id": row["feat_id"],
        "number": row["number"],
        "title": row["title"],
        "description": row.get("description") or "",
        "plain_description": row.get("plain_description"),
        "task_type": row["task_type"],
        "status": row.get("status") or "todo",
        "priority": row.get("priority") or "normal",
        "estimated_minutes": row.get("estimated_minutes"),
        "actual_minutes": row.get("actual_minutes"),
        "checklist_type": row.get("checklist_type"),
        "baseline_sha": row.get("baseline_sha"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _bug_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "version_id": row.get("version_id"),
        "bug_number": row["bug_number"],
        "title": row["title"],
        "description": row["description"],
        "severity": row["severity"],
        "status": row.get("status") or "new",
        "source": row.get("source") or "internal",
        "reported_by": row.get("reported_by"),
        "environment": row.get("environment"),
        "resolved_at": row.get("resolved_at"),
        "commit_hash": row.get("commit_hash"),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _backlog_dict(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "number": row["number"],
        "title": row["title"],
        "description": row.get("description"),
        "priority": row.get("priority") or "medium",
        "status": row.get("status") or "open",
        "version_id": row.get("version_id"),
        "realized_at": row.get("realized_at"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _deploy_dict(row: Mapping) -> dict:
    # seq preserved verbatim (the IDENTITY high-water-mark is advanced after the run).
    return {
        "id": row["id"],
        "seq": row["seq"],
        "customer_id": row["customer_id"],
        "project_id": row["project_id"],
        "version_number": row["version_number"],
        "environment": row["environment"],
        "event_type": row["event_type"],
        "status": row.get("status") or "ok",
        "actor_id": row.get("actor_id"),
        "detail": row.get("detail"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# Credentials subset (flat registry reached via customers.credential_id)
# ---------------------------------------------------------------------------


def _copy_credentials_subset(
    source_conn: Connection,
    source_tables: dict[str, Table],
    target_conn: Connection,
    credential_ids: set[UUID],
) -> int:
    """Copy the referenced credential REGISTRY rows not already present in target.

    Credentials are a flat, project-less registry (reached only via
    ``customers.credential_id``). A credential may be shared across projects and may
    already exist in target from a prior project or a prior run → copy only the
    missing ones (never overwrites; never reads a secret file). Returns the count
    actually inserted.
    """
    if not credential_ids:
        return 0
    target_creds = _target("credentials")
    present = {
        r[0] for r in target_conn.execute(select(target_creds.c.id).where(target_creds.c.id.in_(list(credential_ids))))
    }
    to_copy = [cid for cid in credential_ids if cid not in present]
    if not to_copy:
        return 0
    rows = _select_in(source_conn, source_tables["credentials"], "id", to_copy)
    dicts = [_credential_dict(r) for r in rows]
    _insert_many(target_conn, "credentials", dicts)
    return len(dicts)


# ---------------------------------------------------------------------------
# Public: copy one project's tree
# ---------------------------------------------------------------------------


def copy_project(
    source_conn: Connection,
    source_tables: dict[str, Table],
    target_conn: Connection,
    project_row: Mapping,
    config: MigrationConfig,
) -> dict[str, int]:
    """Copy one project's whole tree into target (FK order). Returns per-table counts.

    The caller (runner) owns the transaction — this issues INSERTs only, so a
    verification failure or DB error can roll the whole tree back atomically.
    """
    pid = project_row["id"]
    counts: dict[str, int] = {}

    # 1. projects
    _insert_many(target_conn, "projects", [_project_dict(project_row, config)])
    counts["projects"] = 1

    # 2. project_members (RBAC — B1)
    members = _select_by(source_conn, source_tables["project_members"], project_id=pid)
    _insert_many(target_conn, "project_members", [_member_dict(r) for r in members])
    counts["project_members"] = len(members)

    # 3. credentials (subset referenced by this project's customers) — BEFORE customers (FK)
    customers = _select_by(source_conn, source_tables["customers"], project_id=pid)
    credential_ids = {r["credential_id"] for r in customers if r.get("credential_id") is not None}
    counts["credentials"] = _copy_credentials_subset(source_conn, source_tables, target_conn, credential_ids)

    # 4. customers
    _insert_many(target_conn, "customers", [_customer_dict(r) for r in customers])
    counts["customers"] = len(customers)

    # 5. versions
    versions = _select_by(source_conn, source_tables["versions"], project_id=pid)
    _insert_many(target_conn, "versions", [_version_dict(r) for r in versions])
    counts["versions"] = len(versions)

    # 6. epics (NO module_id)
    epics = _select_by(source_conn, source_tables["epics"], project_id=pid)
    _insert_many(target_conn, "epics", [_epic_dict(r) for r in epics])
    counts["epics"] = len(epics)

    # 7. feats (under this project's epics)
    epic_ids = [r["id"] for r in epics]
    feats = _select_in(source_conn, source_tables["feats"], "epic_id", epic_ids)
    _insert_many(target_conn, "feats", [_feat_dict(r) for r in feats])
    counts["feats"] = len(feats)

    # 8. tasks (under those feats)
    feat_ids = [r["id"] for r in feats]
    tasks = _select_in(source_conn, source_tables["tasks"], "feat_id", feat_ids)
    _insert_many(target_conn, "tasks", [_task_dict(r) for r in tasks])
    counts["tasks"] = len(tasks)

    # 9. bugs (created_by NOT NULL — pre-flight already asserted the user exists)
    bugs = _select_by(source_conn, source_tables["bugs"], project_id=pid)
    _insert_many(target_conn, "bugs", [_bug_dict(r) for r in bugs])
    counts["bugs"] = len(bugs)

    # 10. backlog_items
    backlog = _select_by(source_conn, source_tables["backlog_items"], project_id=pid)
    _insert_many(target_conn, "backlog_items", [_backlog_dict(r) for r in backlog])
    counts["backlog_items"] = len(backlog)

    # 11. deploy_events (seq preserved; IDENTITY advanced after the apply loop)
    deploys = _select_by(source_conn, source_tables["deploy_events"], project_id=pid)
    _insert_many(target_conn, "deploy_events", [_deploy_dict(r) for r in deploys])
    counts["deploy_events"] = len(deploys)

    return counts


def advance_deploy_seq(target_conn: Connection) -> int | None:
    """Advance the target ``deploy_events.seq`` IDENTITY to the copied high-water-mark (M3).

    deploy_events.seq is the ONLY Identity/serial among the copied tables (deploy.py:67)
    and the load-bearing "latest event" key (deploy service orders by seq.desc()). After
    copying rows with explicit seq, the sequence must be fast-forwarded so the next in-app
    deploy/accept insert cannot collide on the unique seq nor mis-order the UAT gate.
    Returns the new high-water-mark, or None if no deploy_events exist yet.
    """
    max_seq = target_conn.execute(text("SELECT MAX(seq) FROM deploy_events")).scalar()
    if max_seq is None:
        return None
    target_conn.execute(
        text("SELECT setval(pg_get_serial_sequence('deploy_events', 'seq'), :m)"),
        {"m": max_seq},
    )
    return max_seq
