"""Post-copy verification — count parity, deep field compare, pipeline delta.

Runs on the SAME target connection as the copy, INSIDE the per-project transaction
(so in dry-run it verifies the un-committed rows before the rollback, and in apply
it verifies before the commit). Produces a list of findings classified:

* CRITICAL — a count mismatch, a field-level corruption, a missing referenced
  credential row, or a non-zero pipeline delta. Any CRITICAL → the runner rolls
  that project back.
* WARN — a dangling credential file pointer or a missing on-disk dir. Non-blocking.

PIPELINE DELTA (m5): pipeline_state/pipeline_message are version-scoped (no
project_id) and the migration inserts ZERO of them (OQ-6). Rather than asserting
the target is globally empty (false when the v2 target already has forward v2
builds), we assert the BEFORE/AFTER count delta on target == 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import Table, func, select
from sqlalchemy.engine import Connection

from backend.db.base import Base
from backend.services.migration.secrets_guard import credential_file_present

CRITICAL = "CRITICAL"
WARN = "WARN"

# Verbatim-copied columns compared field-by-field (source vs target). Transformed /
# v2-added columns (projects.type/auth_mode/source_path/owner_id/..., *.plain_description,
# tasks.baseline_sha, epics.module_id) are deliberately EXCLUDED — they legitimately
# differ or have no v1 counterpart. Only columns that ALSO exist in the source are
# compared (a v1 source missing a column just skips it).
COMPARE_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": (
        "id",
        "name",
        "slug",
        "status",
        "description",
        "created_by",
        "backend_port",
        "frontend_port",
        "db_port",
        "repo_url",
        "kb_path",
        "created_at",
        "updated_at",
    ),
    "project_members": ("id", "project_id", "user_id", "role", "created_at", "updated_at"),
    "customers": (
        "id",
        "project_id",
        "name",
        "slug",
        "subdomain",
        "integrations",
        "credential_id",
        "notes",
        "created_at",
        "updated_at",
    ),
    "versions": (
        "id",
        "project_id",
        "version_number",
        "name",
        "status",
        "description",
        "target_date",
        "release_date",
        "created_at",
        "updated_at",
    ),
    "epics": ("id", "project_id", "version_id", "number", "title", "status", "created_at", "updated_at"),
    "feats": (
        "id",
        "epic_id",
        "number",
        "title",
        "description",
        "status",
        "estimated_minutes",
        "actual_minutes",
        "task_count",
        "auto_fix_count",
        "created_at",
        "updated_at",
    ),
    "tasks": (
        "id",
        "feat_id",
        "number",
        "title",
        "description",
        "task_type",
        "status",
        "priority",
        "estimated_minutes",
        "actual_minutes",
        "checklist_type",
        "created_at",
        "updated_at",
    ),
    "bugs": (
        "id",
        "project_id",
        "version_id",
        "bug_number",
        "title",
        "description",
        "severity",
        "status",
        "source",
        "reported_by",
        "environment",
        "resolved_at",
        "commit_hash",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "backlog_items": (
        "id",
        "project_id",
        "number",
        "title",
        "description",
        "priority",
        "status",
        "version_id",
        "realized_at",
        "created_at",
        "updated_at",
    ),
    "deploy_events": (
        "id",
        "seq",
        "customer_id",
        "project_id",
        "version_number",
        "environment",
        "event_type",
        "status",
        "actor_id",
        "detail",
        "created_at",
        "updated_at",
    ),
    "credentials": ("id", "title", "file_path", "created_at", "updated_at"),
}


def _target(name: str) -> Table:
    return Base.metadata.tables[name]


def _src_by(conn: Connection, table: Table, **eq) -> list[Mapping]:
    stmt = select(table)
    for col, value in eq.items():
        stmt = stmt.where(table.c[col] == value)
    return list(conn.execute(stmt).mappings().all())


def _src_in(conn: Connection, table: Table, column: str, values: Sequence) -> list[Mapping]:
    if not values:
        return []
    return list(conn.execute(select(table).where(table.c[column].in_(list(values)))).mappings().all())


def _tgt_by(conn: Connection, name: str, **eq) -> list[Mapping]:
    table = _target(name)
    stmt = select(table)
    for col, value in eq.items():
        stmt = stmt.where(table.c[col] == value)
    return list(conn.execute(stmt).mappings().all())


def _tgt_in(conn: Connection, name: str, column: str, values: Sequence) -> list[Mapping]:
    if not values:
        return []
    table = _target(name)
    return list(conn.execute(select(table).where(table.c[column].in_(list(values)))).mappings().all())


def capture_pipeline_counts(conn: Connection) -> dict[str, int]:
    """Snapshot target pipeline_state + pipeline_message counts (for the delta check)."""
    ps = conn.execute(select(func.count()).select_from(_target("pipeline_state"))).scalar_one()
    pm = conn.execute(select(func.count()).select_from(_target("pipeline_message"))).scalar_one()
    return {"pipeline_state": ps, "pipeline_message": pm}


def _compare_rows(
    table: str,
    source_rows: list[Mapping],
    target_rows: list[Mapping],
    source_columns: set[str],
    findings: list[dict],
) -> None:
    """Count-parity + field-by-field deep compare (matched by preserved id)."""
    if len(source_rows) != len(target_rows):
        findings.append(
            {
                "table": table,
                "severity": CRITICAL,
                "code": "count_mismatch",
                "detail": f"source={len(source_rows)} target={len(target_rows)}",
            }
        )
    target_by_id = {r["id"]: r for r in target_rows}
    cols = [c for c in COMPARE_COLUMNS[table] if c in source_columns]
    for s_row in source_rows:
        t_row = target_by_id.get(s_row["id"])
        if t_row is None:
            findings.append({"table": table, "severity": CRITICAL, "code": "row_missing", "id": str(s_row["id"])})
            continue
        for col in cols:
            if s_row[col] != t_row[col]:
                findings.append(
                    {
                        "table": table,
                        "severity": CRITICAL,
                        "code": "field_mismatch",
                        "id": str(s_row["id"]),
                        "detail": f"column={col}",
                    }
                )


def verify_project(
    source_conn: Connection,
    source_tables: dict[str, Table],
    target_conn: Connection,
    project_row: Mapping,
    pipeline_baseline: dict[str, int],
) -> list[dict]:
    """Verify one migrated project's tree. Returns findings (CRITICAL/WARN)."""
    findings: list[dict] = []
    pid = project_row["id"]
    st = source_tables

    def src_cols(name: str) -> set[str]:
        return set(st[name].c.keys())

    # --- project (single row) ---
    t_project = _tgt_by(target_conn, "projects", id=pid)
    _compare_rows("projects", [project_row], t_project, src_cols("projects"), findings)

    # --- direct project-scoped tables ---
    for name in ("project_members", "customers", "versions", "epics", "bugs", "backlog_items", "deploy_events"):
        s_rows = _src_by(source_conn, st[name], project_id=pid)
        t_rows = _tgt_by(target_conn, name, project_id=pid)
        _compare_rows(name, s_rows, t_rows, src_cols(name), findings)

    # --- feats / tasks (reached via epic/feat) ---
    epic_ids = [r["id"] for r in _src_by(source_conn, st["epics"], project_id=pid)]
    s_feats = _src_in(source_conn, st["feats"], "epic_id", epic_ids)
    t_feats = _tgt_in(target_conn, "feats", "epic_id", epic_ids)
    _compare_rows("feats", s_feats, t_feats, src_cols("feats"), findings)

    feat_ids = [r["id"] for r in s_feats]
    s_tasks = _src_in(source_conn, st["tasks"], "feat_id", feat_ids)
    t_tasks = _tgt_in(target_conn, "tasks", "feat_id", feat_ids)
    _compare_rows("tasks", s_tasks, t_tasks, src_cols("tasks"), findings)

    # --- credentials subset (referenced by this project's customers) ---
    s_customers = _src_by(source_conn, st["customers"], project_id=pid)
    cred_ids = {r["credential_id"] for r in s_customers if r.get("credential_id") is not None}
    s_creds = _src_in(source_conn, st["credentials"], "id", list(cred_ids))
    t_creds = _tgt_in(target_conn, "credentials", "id", list(cred_ids))
    _compare_rows("credentials", s_creds, t_creds, src_cols("credentials"), findings)

    # --- credential file existence (existence-only, §4) — WARN, non-blocking ---
    for cred in t_creds:
        if not credential_file_present(cred["file_path"]):
            findings.append(
                {
                    "table": "credentials",
                    "severity": WARN,
                    "code": "dangling_credential_pointer",
                    "id": str(cred["id"]),
                    "file_path": cred["file_path"],
                }
            )

    # --- pipeline delta (m5) — target inserts ZERO pipeline rows ---
    after = capture_pipeline_counts(target_conn)
    for key in ("pipeline_state", "pipeline_message"):
        delta = after[key] - pipeline_baseline.get(key, 0)
        if delta != 0:
            findings.append(
                {
                    "table": key,
                    "severity": CRITICAL,
                    "code": "pipeline_delta_nonzero",
                    "detail": f"delta={delta}",
                }
            )

    return findings
