#!/usr/bin/env python3
"""Deploy a UAT environment from a project's current code (manual ops CLI).

v0.9.0 Phase 2 (CR-2): this is now a **thin wrapper** over the importable provisioner
:func:`backend.services.uat_provisioner.provision_uat` — the rendering/detection logic moved there
so the engine (Phase 3) can provision in-process. The CLI keeps the manual-ops orchestration the
provisioner deliberately does NOT do: port allocation, pre-deploy snapshot, ``docker compose
build``/``up``, and wait-healthy.

Routing is via Traefik (Phase-1 infra) — there is **no nginx step** any more; the rendered compose
carries the Traefik labels + joins ``nex-proxy-net``. Per CR-NS-061 a redeploy PRESERVES the
existing instance's secrets + backend ``extra_hosts`` by default (``--rotate-secrets`` forces a
fresh re-provision).

Usage:
    python scripts/uat-deploy.py <slug>
    python scripts/uat-deploy.py mager --project nex-inbox
    python scripts/uat-deploy.py dev --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
NEX_STUDIO_ROOT = SCRIPTS_DIR.parent
for _p in (str(SCRIPTS_DIR), str(NEX_STUDIO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _uat_lib  # noqa: E402

from backend.services import uat_provisioner  # noqa: E402

UAT_ROOT = Path("/opt/uat")
PROJECTS_ROOT = Path("/opt/projects")


def check_uat_root_exists() -> bool:
    """Verify /opt/uat/ exists + is writable. Returns False with hint if not."""
    if not UAT_ROOT.exists():
        _uat_lib.console.print(
            f"[red]ERROR:[/red] {UAT_ROOT} neexistuje. "
            "Spusti pre-deploy setup per docs/runbooks/UAT_FIRST_TIME_SETUP.md:"
        )
        _uat_lib.console.print("  [yellow]sudo mkdir -p /opt/uat && sudo chown $USER:$USER /opt/uat[/yellow]")
        return False
    return True


def resolve_project(*, slug: str, project: str | None) -> str:
    """Resolve project name from slug + optional --project override."""
    return project if project else slug


def _print_summary(result: uat_provisioner.ProvisionResult, *, version: str, dry_run: bool) -> None:
    slug = result.uat_slug
    mode = "[yellow]DRY-RUN[/yellow]" if dry_run else "[green]DEPLOYED[/green]"
    _uat_lib.console.print(f"\n=== UAT {mode} for slug={slug} (v{version}) ===")
    rows = {
        "URL": f"https://uat-{slug}.isnex.eu",
        "Routing": "Traefik (nex-proxy-net) — no manual nginx step",
        "Services": ", ".join(result.services),
        "Compose": str(result.compose_path),
    }
    if result.loopback_base_port is not None:
        base = result.loopback_base_port
        rows["Frontend port (local)"] = f"127.0.0.1:{base}"
        rows["Backend port (local)"] = f"127.0.0.1:{base + 100} → {result.be_internal_port}"
        rows["Postgres port (local)"] = f"127.0.0.1:{base + 200}"
    _uat_lib.console.print(_uat_lib.status_table(rows))
    for warning in result.warnings:
        _uat_lib.console.print(f"[yellow]WARNING:[/yellow] {warning}")


def deploy(
    slug: str,
    *,
    project: str | None = None,
    dry_run: bool = False,
    version: str = "v0.0.0-dev",
    rotate_secrets: bool = False,
) -> int:
    """Deploy orchestrator (thin wrapper): allocate port → provision (render) → snapshot → build →
    up → wait-healthy → summary. Returns 0 on success, 1 on any failure (port released on a
    post-allocation failure to prevent a state leak)."""
    _uat_lib.validate_slug(slug)

    if not check_uat_root_exists():
        return 1

    resolved_project = resolve_project(slug=slug, project=project)
    _uat_lib.validate_slug(resolved_project)

    project_path = PROJECTS_ROOT / resolved_project
    if not project_path.exists():
        _uat_lib.console.print(f"[red]ERROR:[/red] project directory not found: {project_path}")
        return 1

    port = _uat_lib.allocate_port(slug)
    _uat_lib.console.print(f"[cyan]Port allocated:[/cyan] {port}")

    if dry_run:
        # Render in-memory to a temp area so the plan is real, but never touch /opt/uat or docker.
        try:
            source = uat_provisioner.load_source_compose(project_path)
        except (FileNotFoundError, ValueError) as exc:
            _uat_lib.error_console.print(f"[red]ERROR:[/red] {exc}")
            _uat_lib.release_port(slug)
            return 1
        roles = uat_provisioner.identify_service_roles(source["services"])
        result = uat_provisioner.ProvisionResult(
            uat_slug=slug,
            uat_dir=UAT_ROOT / slug,
            compose_path=UAT_ROOT / slug / "docker-compose.yml",
            env_path=UAT_ROOT / slug / ".env",
            services=list(source["services"].keys()),
            fe_service=roles["frontend"],
            be_service=roles["backend"],
            db_service=roles["db"],
            be_internal_port=(
                uat_provisioner.detect_internal_port(source["services"][roles["backend"]], 8000)
                if roles["backend"]
                else None
            ),
            fe_internal_port=None,
            be_health_path="/health",
            loopback_base_port=port,
            is_redeploy=(UAT_ROOT / slug / ".env").is_file(),
            warnings=[],
        )
        _print_summary(result, version=version, dry_run=True)
        return 0

    # Post-allocation: any failure must release the port to prevent state leak.
    try:
        result = uat_provisioner.provision_uat(
            resolved_project,
            slug,
            version=version,
            projects_root=PROJECTS_ROOT,
            uat_root=UAT_ROOT,
            loopback_base_port=port,
            rotate_secrets=rotate_secrets,
        )
        if result.is_redeploy:
            _uat_lib.console.print(
                "[cyan]redeploy:[/cyan] preserved existing secrets + backend extra_hosts "
                "(use --rotate-secrets to regenerate)"
            )
        for warning in result.warnings:
            _uat_lib.console.print(f"[yellow]WARNING:[/yellow] {warning}")

        # Pre-deploy snapshot of the existing DB (best-effort — skip if no container yet).
        if result.db_service:
            db_container = f"uat-{slug}-{result.db_service}"
            env = _uat_lib.read_uat_env(slug)
            try:
                dump = _uat_lib.docker_exec(
                    db_container,
                    ["pg_dump", "-U", env.get("POSTGRES_USER", "postgres")],
                    capture=True,
                )
                snapshot_path = (
                    result.uat_dir / "snapshots" / _uat_lib.snapshot_filename(version=version, reason="pre-redeploy")
                )
                snapshot_path.write_bytes(dump.stdout.encode("utf-8") if isinstance(dump.stdout, str) else dump.stdout)
                snapshot_path.chmod(0o600)
                _uat_lib.console.print(f"[cyan]Pre-deploy snapshot:[/cyan] {snapshot_path}")
            except Exception:  # noqa: BLE001 — no existing container → first-time deploy.
                pass

        # Build + start. Export APP_VERSION / VITE_APP_VERSION so source build-args resolve to the
        # real version (the FE host-build + backend image-arg bake the same value).
        app_version = _uat_lib.git_describe(project_path)
        build_env = {"APP_VERSION": app_version, "VITE_APP_VERSION": app_version}
        _uat_lib.docker_compose(["build"], cwd=result.uat_dir, env=build_env)
        _uat_lib.docker_compose(["up", "-d"], cwd=result.uat_dir, env=build_env)

        # Wait healthy on the backend loopback port + its resolved health path.
        if result.be_internal_port is not None:
            probe = f"http://127.0.0.1:{port + 100}{result.be_health_path}"
            if not _uat_lib.wait_healthy(probe, timeout=120):
                _uat_lib.error_console.print(
                    "[red]ERROR:[/red] backend not healthy after 120s — check logs:\n"
                    f"  docker compose -f {result.compose_path} logs"
                )
                _uat_lib.release_port(slug)
                return 1
    except Exception as exc:  # noqa: BLE001
        _uat_lib.error_console.print(f"[red]ERROR:[/red] deploy failed: {exc}")
        _uat_lib.release_port(slug)
        _uat_lib.error_console.print(f"[yellow]Port released for slug={slug}.[/yellow]")
        return 1

    _print_summary(result, version=version, dry_run=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy a UAT environment from a project's current code (v0.9.0 Phase 2 thin wrapper).",
    )
    parser.add_argument("slug", help="UAT slug (lowercase, kebab-case, e.g. 'mager', 'dev')")
    parser.add_argument("--project", default=None, help="Source project name (default: same as slug)")
    parser.add_argument(
        "--version",
        default="v0.0.0-dev",
        help="Project version tag for snapshots + env (e.g. 'v0.2.0')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without invoking docker / writing files",
    )
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Force FRESH secrets (true re-provision). By DEFAULT an existing instance's secrets + "
        "backend extra_hosts are PRESERVED on redeploy (CR-NS-061) — never silently rotate a "
        "data-bearing instance.",
    )
    args = parser.parse_args()

    try:
        return deploy(
            args.slug,
            project=args.project,
            dry_run=args.dry_run,
            version=args.version,
            rotate_secrets=args.rotate_secrets,
        )
    except ValueError as exc:
        _uat_lib.error_console.print(f"[red]ERROR:[/red] slug: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
