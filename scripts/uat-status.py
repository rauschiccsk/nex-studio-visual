#!/usr/bin/env python3
"""Zobraziť stav UAT prostredia (containers + URL + snapshots + disk).

Per F-003 §4.3 spec — read-only status query.

Spustenie:
    python scripts/uat-status.py <slug>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _uat_lib  # noqa: E402

UAT_ROOT = Path("/opt/uat")


def _read_env_value(env_path: Path, key: str) -> str | None:
    """Parse simple KEY=VALUE from a dotenv-style file. Returns None if missing."""
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def _get_container_statuses(slug: str) -> list[dict[str, str]]:
    """Return list of {Names, Status} dicts for UAT containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--all", "--filter", f"name=uat-{slug}-", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    containers: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append({"name": data.get("Names", ""), "status": data.get("Status", "")})
    return containers


def _get_snapshots_info(slug: str) -> dict[str, object]:
    snapshots_dir = UAT_ROOT / slug / "snapshots"
    if not snapshots_dir.exists():
        return {"count": 0, "latest": None, "total_bytes": 0}
    files = sorted(snapshots_dir.glob("*.sql.gz"))
    if not files:
        return {"count": 0, "latest": None, "total_bytes": 0}
    total = sum(f.stat().st_size for f in files)
    return {
        "count": len(files),
        "latest": files[-1].name,  # sorted alphabetically → date-sorted given naming convention
        "total_bytes": total,
    }


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def status(slug: str) -> int:
    _uat_lib.validate_slug(slug)

    uat_dir = UAT_ROOT / slug
    compose = uat_dir / "docker-compose.yml"
    env_file = uat_dir / ".env"

    if not compose.exists():
        # NOT DEPLOYED — still show snapshots if they exist
        snap_info = _get_snapshots_info(slug)
        _uat_lib.console.print(f"\n=== UAT [yellow]NOT DEPLOYED[/yellow] for slug={slug} ===")
        if snap_info["count"]:
            _uat_lib.console.print(f"Last snapshot: {snap_info['latest']} (existuje, ready for restore)")
        return 0

    containers = _get_container_statuses(slug)
    running = bool(containers) and all(c["status"].startswith("Up ") for c in containers)
    state = "RUNNING" if running else "STOPPED"
    color = "green" if running else "yellow"

    version = _read_env_value(env_file, "PROJECT_VERSION") or "unknown"
    port = _uat_lib.get_allocated_port(slug)
    snap_info = _get_snapshots_info(slug)

    _uat_lib.console.print(f"\n=== UAT [{color}]{state}[/{color}] for slug={slug} (v{version}) ===")
    _uat_lib.console.print(
        _uat_lib.status_table(
            {
                "URL": f"https://uat-{slug}.isnex.eu",
                "Frontend port (local)": str(port) if port else "not allocated",
                "Containers": (
                    "\n".join(f"  {c['name']:<30} {c['status']}" for c in containers) if containers else "(none)"
                ),
                "Snapshots count": str(snap_info["count"]),
                "Snapshots latest": str(snap_info["latest"] or "(none)"),
                "Snapshots total size": _format_bytes(int(snap_info["total_bytes"])),
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zobraziť stav UAT prostredia (F-003 §4.3).",
    )
    parser.add_argument("slug", help="UAT slug (e.g. 'mager', 'dev')")
    args = parser.parse_args()

    try:
        return status(args.slug)
    except ValueError as exc:
        _uat_lib.error_console.print(f"[red]ERROR:[/red] slug: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
