#!/usr/bin/env python3
"""STEP 8 — migrate the real projects from a v1 NEX Studio DB into a fresh v2 DB.

A SEATBELT data-copy CLI. Dry-run is the DEFAULT (rehearses the whole migration in
a target transaction and rolls back, printing the plan). ``--apply`` commits
per-project. The two DB URLs are explicit args (the tool needs two engines; it does
NOT read the single hardcoded ``settings.database_url``).

Examples::

    # Dry-run (default) — prints exactly what WOULD happen, writes nothing:
    python scripts/migrate_v1_to_v2.py \\
        --source-url postgresql+pg8000://u:p@localhost:9178/nexstudio \\
        --target-url postgresql+pg8000://u:p@localhost:9198/nexstudio_v2

    # Apply — commits per-project (after you have confirmed the dry-run + backed up):
    python scripts/migrate_v1_to_v2.py --source-url ... --target-url ... --apply

Safety (see docs/architecture/step8-migration-design.md):
  * two fail-closed DB-name guards + a FULL referenced-user pre-flight;
  * per-project transactions (a failure isolates to one project);
  * NO secret value is ever read/logged (§4) — only credential registry pointers.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Make ``backend`` importable when run directly as ``python scripts/...``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.services.migration import MigrationConfig, run_migration  # noqa: E402
from backend.services.migration.config import DEFAULT_PROJECTS_ROOT_V2  # noqa: E402
from backend.services.migration.runner import MigrationGuardError, MigrationReport  # noqa: E402

# On-disk copy source root (v1 projects live here). The DB copy rebases source_path
# onto ``--projects-root``; the OPT-IN --copy-dirs also physically copies the tree.
_V1_PROJECTS_ROOT = "/opt/projects"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_v1_to_v2.py",
        description="Copy v1 NEX Studio projects into a fresh v2 database (dry-run by default).",
    )
    parser.add_argument("--source-url", required=True, help="v1 source DB URL (read-only).")
    parser.add_argument("--target-url", required=True, help="v2 target DB URL (written per-project on --apply).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit per-project. WITHOUT this flag the tool dry-runs (rolls back, prints the plan).",
    )
    parser.add_argument(
        "--projects-root",
        default=DEFAULT_PROJECTS_ROOT_V2,
        help=f"v2 projects root for rebased source_path (default {DEFAULT_PROJECTS_ROOT_V2}).",
    )
    parser.add_argument(
        "--only-slug",
        action="append",
        default=[],
        metavar="SLUG",
        help="Restrict to this project slug (repeatable).",
    )
    parser.add_argument(
        "--exclude-status",
        action="append",
        default=[],
        metavar="STATUS",
        help="Skip source projects with this status (repeatable, e.g. archived).",
    )
    parser.add_argument(
        "--copy-dirs",
        action="store_true",
        help="OPT-IN: also copy /opt/projects/<slug> → <projects-root>/<slug> on disk (default OFF).",
    )
    parser.add_argument("--report-path", default=None, help="Where to write the JSON report.")
    parser.add_argument(
        "--i-understand-target-is-prod",
        action="store_true",
        help="Override the guard that refuses a target whose DB name equals the cockpit PROD DB.",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> MigrationConfig:
    return MigrationConfig(
        source_url=args.source_url,
        target_url=args.target_url,
        projects_root=args.projects_root,
        dry_run=not args.apply,
        only_slugs=tuple(args.only_slug),
        exclude_statuses=tuple(args.exclude_status),
        copy_dirs=args.copy_dirs,
        report_path=args.report_path,
        allow_prod_target=args.i_understand_target_is_prod,
    )


def print_report(report: MigrationReport) -> None:
    """Print a human-readable summary (never any secret value)."""
    mode = "DRY-RUN (rolled back — nothing written)" if report.dry_run else "APPLY (committed per-project)"
    print(f"\n=== Migration {mode} ===")
    print(f"source DB: {report.source_db}   →   target DB: {report.target_db}")
    print(f"generated: {report.generated_at}")
    for p in report.projects:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(p.counts.items())) if p.counts else "—"
        print(f"  [{p.status.upper():8}] {p.slug}: {counts}" + (f"  ({p.reason})" if p.reason else ""))
        warns = [f for f in p.findings if f.get("severity") == "WARN"]
        crits = [f for f in p.findings if f.get("severity") == "CRITICAL"]
        for f in crits:
            print(f"        CRITICAL {f.get('table')}: {f.get('code')} {f.get('detail', '')}".rstrip())
        for f in warns:
            print(f"        WARN {f.get('table')}: {f.get('code')} {f.get('file_path', '')}".rstrip())
    print(f"\noverall: {report.overall} (exit {report.exit_code})")
    if report.report_path:
        print(f"report: {report.report_path}")


def _copy_dirs(report: MigrationReport, config: MigrationConfig) -> None:
    """OPT-IN on-disk copy of each migrated project's tree (git-idle precondition).

    Only runs for genuinely migrated projects on --apply. Requires the source tree to
    be git-idle (clean working tree) before copying, and prints a post-copy
    ``git status`` health check on the destination.
    """
    import shutil

    migrated = [p.slug for p in report.projects if p.status == "migrated"]
    if not migrated:
        print("\n--copy-dirs: no migrated projects to copy.")
        return
    print("\n--copy-dirs: copying on-disk trees...")
    for slug in migrated:
        src = Path(_V1_PROJECTS_ROOT) / slug
        dst = Path(config.projects_root) / slug
        if not src.is_dir():
            print(f"  WARN {slug}: source dir {src} missing — skipped.")
            continue
        if dst.exists():
            print(f"  WARN {slug}: destination {dst} already exists — skipped (no overwrite).")
            continue
        # git-idle precondition — refuse to copy a dirty working tree.
        status = subprocess.run(
            ["git", "-C", str(src), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            print(f"  WARN {slug}: source git tree not idle — skipped (quiesce v1 first).")
            continue
        shutil.copytree(src, dst)
        health = subprocess.run(
            ["git", "-C", str(dst), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        note = "clean" if not health.stdout.strip() else "has changes (review)"
        print(f"  OK {slug}: {src} → {dst} (git: {note})")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_args(args)
    try:
        report = run_migration(config)
    except MigrationGuardError as exc:
        print(f"\nPRE-FLIGHT ABORT: {exc}", file=sys.stderr)
        return 3
    print_report(report)
    if config.copy_dirs and not config.dry_run:
        _copy_dirs(report, config)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
