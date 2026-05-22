#!/usr/bin/env python3
"""Ad-hoc DB snapshot pre UAT slug mimo cleanup cyklu.

Per F-003 §4.5 spec — pred riskantnou zmenou konfigurácie, pred testovaním
edge case, alebo na žiadosť Direktora.

Per Sub-round 4 O-003-2: forever retention (žiadny auto-cleanup).

Spustenie:
    python scripts/uat-snapshot.py <slug>
    python scripts/uat-snapshot.py mager --reason before-experimental-config
    python scripts/uat-snapshot.py dev --version v0.2.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _uat_lib  # noqa: E402

UAT_ROOT = Path("/opt/uat")


def snapshot(slug: str, *, reason: str | None, version: str) -> int:
    """Create ad-hoc snapshot. Returns 0 on success, 1 on failure."""
    _uat_lib.validate_slug(slug)

    uat_dir = UAT_ROOT / slug
    if not (uat_dir / "docker-compose.yml").exists():
        _uat_lib.error_console.print(
            f"[red]ERROR:[/red] UAT not deployed for slug={slug!r} ({uat_dir / 'docker-compose.yml'} not found)"
        )
        return 1

    snapshots_dir = uat_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # Default reason "ad-hoc" per F-003 §8 convention
    effective_reason = reason if reason else "ad-hoc"
    filename = _uat_lib.snapshot_filename(version=version, reason=effective_reason)
    snapshot_path = snapshots_dir / filename

    container = f"uat-{slug}-postgres"
    try:
        result = _uat_lib.docker_exec(container, ["pg_dump", "-U", "postgres"], capture=True)
    except Exception as exc:  # noqa: BLE001
        _uat_lib.error_console.print(f"[red]ERROR:[/red] pg_dump failed: {exc}")
        return 1

    data = result.stdout if isinstance(result.stdout, (bytes, bytearray)) else (result.stdout or "").encode("utf-8")
    snapshot_path.write_bytes(data)
    snapshot_path.chmod(0o600)

    _uat_lib.console.print(f"[green]Snapshot saved:[/green] {snapshot_path}")
    size = snapshot_path.stat().st_size
    _uat_lib.console.print(f"  Size: {size / 1024:.1f} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ad-hoc UAT DB snapshot (F-003 §4.5).",
    )
    parser.add_argument("slug", help="UAT slug (e.g. 'mager', 'dev')")
    parser.add_argument(
        "--reason",
        default=None,
        help="Reason suffix for filename (e.g. 'before-experimental-config')",
    )
    parser.add_argument(
        "--version",
        default="unknown",
        help="Version tag for filename (e.g. 'v0.2.0')",
    )
    args = parser.parse_args()

    try:
        return snapshot(args.slug, reason=args.reason, version=args.version)
    except ValueError as exc:
        _uat_lib.error_console.print(f"[red]ERROR:[/red] slug: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
