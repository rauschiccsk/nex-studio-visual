"""Shared helpers for UAT CLI nástroje (F-003).

Per F-003 §3-§4 spec + Sub-round 4 O-DS-2 (Python + rich) + O-003-2 (forever
snapshot retention).

Public API:
- Slug validation: validate_slug()
- Path utilities: uat_dir, snapshots_dir, project_dir, uat_compose_path,
  nginx_config_path
- Port allocation: allocate_port, release_port, get_allocated_port
- Snapshot filenames: snapshot_filename
- Subprocess wrappers: docker_compose, docker_exec, wait_healthy
- Template rendering: render_template
- Rich UI: console, confirm, status_table, print_url

State files:
- PORT_STATE_FILE: /opt/projects/nex-studio/.uat-ports.json (gitignored)
- TEMPLATES_DIR: /opt/projects/nex-studio/templates
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import yaml
from rich.console import Console
from rich.table import Table

NEX_STUDIO_ROOT = Path(__file__).resolve().parents[1]
PORT_STATE_FILE = NEX_STUDIO_ROOT / ".uat-ports.json"
TEMPLATES_DIR = NEX_STUDIO_ROOT / "templates"

DEFAULT_PORT_RANGE_START = 19500
DEFAULT_PORT_RANGE_END = 19599

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

console = Console()
error_console = Console(stderr=True)


# ---------- Slug validation ----------


def validate_slug(slug: str) -> None:
    """Raise ValueError if slug is not a valid UAT slug.

    Rules:
    - Non-empty
    - Lowercase ASCII letters, digits, hyphens
    - Must start with letter or digit (no leading hyphen, dot, slash)
    """
    if not slug:
        raise ValueError("slug must not be empty")
    if slug != slug.lower():
        raise ValueError(f"slug must be lowercase: {slug!r}")
    if "/" in slug:
        raise ValueError(f"slug contains slash (invalid char): {slug!r}")
    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"slug invalid char (allowed: a-z, 0-9, hyphen, no leading hyphen): {slug!r}")


# ---------- Path utilities ----------


def uat_dir(slug: str) -> Path:
    validate_slug(slug)
    return Path("/opt/uat") / slug


def snapshots_dir(slug: str) -> Path:
    return uat_dir(slug) / "snapshots"


def project_dir(project: str) -> Path:
    validate_slug(project)
    return Path("/opt/projects") / project


def uat_compose_path(slug: str) -> Path:
    return uat_dir(slug) / "docker-compose.yml"


def nginx_config_path(slug: str) -> Path:
    """Final NGINX sites-available path (root-owned — sudo required to write).

    Used as documentation target for Direktor manual sudo cp. Skripty NIKDY
    nezapisujú priamo sem — používajú local_nginx_config_path() (user-writable).
    """
    validate_slug(slug)
    return Path("/etc/nginx/sites-available") / f"uat-{slug}.conf"


def local_nginx_config_path(slug: str) -> Path:
    """User-writable nginx config path: /opt/uat/<slug>/nginx-uat-vhost.conf.

    Skripty zapisujú sem (Implementer scope = no sudo). Direktor manuálne
    `sudo cp` do nginx_config_path() pri NGINX aktivácii (per F-003 §10).
    """
    return uat_dir(slug) / "nginx-uat-vhost.conf"


# ---------- CR-021: per-projekt backend config auto-detection ----------


def detect_backend_config(source_project_path: Path) -> dict[str, Any]:
    """Auto-detect backend port + healthcheck + dockerfile from source compose.

    Per F-003 §4.1 + CR-021 amendment: parse `<source>/docker-compose.yml`
    services.backend section to drive UAT compose rendering. Fallback when
    source compose is missing or has no backend service.

    Returns dict with keys:
        backend_port: int (container port — last segment in host:container mapping)
        healthcheck_test: list[str] | None (None → caller derives default)
        dockerfile: str (relative path to Dockerfile, default "Dockerfile")

    Defaults: {"backend_port": 8000, "healthcheck_test": None, "dockerfile": "Dockerfile"}
    """
    defaults: dict[str, Any] = {
        "backend_port": 8000,
        "healthcheck_test": None,
        "dockerfile": "Dockerfile",
    }

    compose_path = source_project_path / "docker-compose.yml"
    if not compose_path.exists():
        return defaults

    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return defaults

    backend = data.get("services", {}).get("backend") or {}

    # Port: first mapping → container side is LAST segment of "host:container"
    backend_port = defaults["backend_port"]
    for mapping in backend.get("ports", []) or []:
        if isinstance(mapping, str):
            backend_port = int(mapping.split(":")[-1])
            break
        if isinstance(mapping, dict) and "target" in mapping:
            backend_port = int(mapping["target"])
            break

    # Healthcheck (re-use source-defined test as-is)
    healthcheck = backend.get("healthcheck") or {}
    healthcheck_test = healthcheck.get("test")

    # Dockerfile path (build.dockerfile, fallback "Dockerfile")
    build = backend.get("build") or {}
    dockerfile = build.get("dockerfile", defaults["dockerfile"]) if isinstance(build, dict) else defaults["dockerfile"]

    return {
        "backend_port": backend_port,
        "healthcheck_test": healthcheck_test,
        "dockerfile": dockerfile,
    }


# ---------- Port allocation ----------


def _load_port_state() -> dict[str, int]:
    if not PORT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(PORT_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_port_state(state: dict[str, int]) -> None:
    PORT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def allocate_port(
    slug: str,
    *,
    range_start: int = DEFAULT_PORT_RANGE_START,
    range_end: int = DEFAULT_PORT_RANGE_END,
) -> int:
    """Allocate next free port in range for slug. Idempotent: existing slug returns same port."""
    validate_slug(slug)
    state = _load_port_state()

    if slug in state and range_start <= state[slug] <= range_end:
        return state[slug]

    used_ports = set(state.values())
    for port in range(range_start, range_end + 1):
        if port not in used_ports:
            state[slug] = port
            _save_port_state(state)
            return port

    raise RuntimeError(f"port range {range_start}-{range_end} exhausted (no free port for {slug!r})")


def release_port(slug: str) -> None:
    """Remove slug from allocation state. No-op if not allocated."""
    state = _load_port_state()
    if slug in state:
        del state[slug]
        _save_port_state(state)


def get_allocated_port(slug: str) -> int | None:
    """Return allocated port for slug, or None if not allocated."""
    return _load_port_state().get(slug)


# ---------- Snapshot filename ----------


def snapshot_filename(
    version: str,
    *,
    reason: str | None = None,
    teardown: bool = False,
) -> str:
    """Build snapshot filename per F-003 §8 convention.

    Examples:
        v0.1.0-2026-06-15.sql.gz
        v0.1.0-2026-06-15-before-experimental.sql.gz
        v0.1.0-2026-06-15-teardown.sql.gz
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [version, date]
    if reason:
        parts.append(reason)
    if teardown:
        parts.append("teardown")
    return "-".join(parts) + ".sql.gz"


# ---------- Subprocess wrappers ----------


def docker_compose(
    args: list[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run `docker compose <args>` in given cwd. Raises CalledProcessError on non-zero."""
    cmd = ["docker", "compose", *args]
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=capture,
        text=True,
    )


def docker_exec(
    container: str,
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run `docker exec <container> <command>`. Raises CalledProcessError on non-zero."""
    cmd = ["docker", "exec", container, *command]
    return subprocess.run(cmd, check=True, capture_output=capture, text=True)


def wait_healthy(
    url: str,
    *,
    timeout: int = 120,
    interval: float = 5.0,
) -> bool:
    """Poll url until 2xx response or timeout. Returns True if healthy, False if timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=interval) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:  # noqa: BLE001 — any network/HTTP error means not-yet-healthy
            pass
        time.sleep(interval)
    return False


# ---------- Template rendering ----------


def render_template(template_name: str, context: dict[str, str]) -> str:
    """Render Jinja2 template from TEMPLATES_DIR with given context.

    template_name is relative to TEMPLATES_DIR (e.g. "uat/docker-compose.yml.j2").
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    template = env.get_template(template_name)
    return template.render(**context)


# ---------- Rich UI ----------


def confirm(prompt: str, *, default: bool = False) -> bool:
    """Interactive yes/no prompt. Returns default when stdin is non-interactive."""
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    response = input(prompt + suffix).strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def status_table(data: dict[str, Any]) -> Table:
    """Build a rich Table for key/value status output."""
    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    for key, value in data.items():
        table.add_row(str(key), str(value))
    return table


def print_url(url: str) -> None:
    """Print URL in a highlighted block."""
    console.print(f"[bold green]URL:[/bold green] [link={url}]{url}[/link]")
