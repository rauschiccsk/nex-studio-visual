"""Isolated LIVE-PREVIEW Vite dev-server sandbox for the ``vizual`` stage (CR-1 sub-task 2).

The ``vizual`` stage (``docs/specs/cr1-faza2-skeleton-spec.md`` §3.C) shows a project's frontend
**live** in the cockpit while the AI edits its source: a manager watches the app, asks for a change
in chat, the AI applies it, and it hot-reloads through Vite HMR in <1 s with **no rebuild**. This
module is the net-new "living dev-server sandbox" that makes that possible.

Proven mechanism (PoC passed 2026-07-12): ``node:20`` with the project ``frontend/`` bind-mounted at
``/app`` running ``vite`` serves the FE and reflects a HOST source edit in ~0.03 s — Vite's file
watcher crosses the bind mount natively (no ``CHOKIDAR_USEPOLLING`` needed). This module wraps that in
the two disciplines the real thing needs:

  * **Isolation** (mirrors :mod:`backend.services.consult_sandbox`): the slug is validated and the
    resolved host source is containment-asserted BEFORE it is composed into a ``-v`` bind; the ONLY
    bind mounts are the project ``frontend/`` (rw — HMR must see host edits) + one tiny generated Vite
    override config (ro); the container joins ONLY ``nex-proxy-net``. Deliberately ABSENT (the negative
    half of the guarantee — asserted by the tests): NO ``/var/run/docker.sock``, NO ``/opt/customers``,
    NO ``/opt/uat``, NO ``/opt/infra``, NO credentials store, NO knowledge mount, NO extra network.

  * **Traefik routing** (mirrors :mod:`backend.services.uat_provisioner`): the container carries the
    exact Traefik labels routing ``Host(vizual-<slug>.isnex.eu)`` → the internal Vite port on the
    external :data:`~backend.services.uat_provisioner.PROXY_NETWORK`.

**HMR through the reverse proxy** — the load-bearing detail. A Vite dev-server behind a reverse proxy
on a *different* public host must be told the PUBLIC origin for its HMR WebSocket, or the browser's
live-reload silently no-ops (the page renders, but edits never hot-update). Vite reads this from config
(``server.hmr.{host,clientPort,protocol}``), not from the CLI. We therefore generate a tiny override
config (:func:`_render_override_config`) that **extends the project's own ``vite.config.ts``** (via
``mergeConfig`` — so react/tailwind/aliases stay intact and the manager sees the REAL app) and injects
``server.hmr`` + ``server.allowedHosts`` for the public host. The public origin is HTTPS/443 (TLS is
terminated by the host edge proxy in front of Traefik's cleartext ``web`` entrypoint, exactly as UAT
``*.isnex.eu`` routes work), so HMR is ``wss`` on client port ``443``. Traefik forwards the WebSocket
upgrade for the matched router by default, so no extra label is required.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

#: The ICC-canonical kebab-case project-slug rule, reused verbatim (DRY) — identical to the rule
#: :mod:`backend.services.consult_sandbox` guards its bind source with. Rejects ``..`` / ``/`` / empty /
#: anything non-slug BEFORE it is composed into a ``-v`` bind source.
from backend.services.project_specs import _SLUG_RE as _PROJECT_SLUG_RE

#: Traefik/infra constants — single source of truth in the UAT provisioner (DRY).
from backend.services.uat_provisioner import PROXY_NETWORK, UAT_DOMAIN_SUFFIX

logger = logging.getLogger(__name__)

#: Host root under which every managed project lives. The nex-studio-visual backend bind-mounts this
#: IDENTITY (``/opt/projects`` → ``/opt/projects``, verified via ``docker inspect``), so a host-daemon
#: ``docker run`` resolves the same absolute path the backend sees — no container→host translation is
#: needed for this deployment (unlike the legacy nex-studio ``/opt/projects-v3`` split).
PROJECTS_ROOT = Path("/opt/projects")

#: The port Vite listens on INSIDE the sandbox (Traefik load-balances to it over ``nex-proxy-net``).
VITE_INTERNAL_PORT = 5173

#: The dev-server base image (the PoC image). node:20's ``node`` user is uid/gid 1000 — the same as the
#: host ``andros`` who owns the project files — so ``--user 1000:1000`` writes Vite's caches/temp files
#: as the owning user (never root), keeping the bind-mounted ``frontend/`` clean of root-owned artefacts.
NODE_IMAGE = "node:20"
SANDBOX_USER = "1000:1000"

#: Container / router / public-host naming. ``vizual-<slug>`` for all three (no dots → a valid Traefik
#: router id).
_CONTAINER_PREFIX = "vizual-"

#: Public origin scheme + the derived HMR transport. TLS is terminated by the host edge proxy in front of
#: Traefik's cleartext ``web`` entrypoint, so the browser sees ``https://<host>`` on 443 and the HMR
#: WebSocket must be ``wss`` on client port 443 (a plain ``ws``/80 client would be blocked as mixed-content
#: and never connect). Matches how every UAT ``*.isnex.eu`` route is reached.
_PUBLIC_SCHEME = "https"
_HMR_PROTOCOL = "wss"
_HMR_CLIENT_PORT = 443

#: The override config is written as a REAL file at the frontend ROOT (``frontend/<this name>``), NOT
#: bind-mounted as a separate file. Two hard reasons force the frontend root: (1) the override imports the
#: project's own ``./vite.config.ts`` and re-runs its plugins, so it needs node_modules resolvable from its
#: dir; (2) a project alias like ``'@': path.resolve(__dirname, './src')`` is bundled with ``__dirname`` =
#: the override file's dir — only at the frontend root does that equal the real config's dir, so the alias
#: resolves for ANY project without hardcoding it. It is NOT a second bind mount on purpose: bind-mounting a
#: FILE into the already-bind-mounted ``/app`` makes Docker create the mount point as a stray **root-owned
#: 0-byte file** in the host ``frontend/`` that survives container removal — real project pollution. Writing
#: a real file the service owns end-to-end (created on spin-up, git-excluded, deleted on teardown) avoids it.
_OVERRIDE_CONTAINER_NAME = "vite.vizual.config.mts"

#: Vite's scratch dirs under node_modules, shadowed by writable ``tmpfs`` mounts. Two reasons: (1) Vite
#: writes here — the config-bundling temp (``.vite-temp``, written BEFORE our config even loads, so it is
#: NOT configurable away) and the optimized-deps cache (``.vite``) — and running unprivileged (uid 1000)
#: must NEVER be blocked by pre-existing root-owned scratch dirs a prior root-run Vite may have left in the
#: shared ``node_modules`` (an ``EACCES`` that kills the dev server); (2) it keeps the bind-mounted host
#: ``node_modules`` PRISTINE — all Vite churn lives in ephemeral RAM, cleared on teardown, so the AI's
#: project tree is never polluted with cache artefacts. Mode 1777 so uid 1000 can create files inside.
_VITE_SCRATCH_DIRS = ("/app/node_modules/.vite", "/app/node_modules/.vite-temp")

#: Bound the one-shot ``npm install`` fallback (node_modules is normally scaffolded already).
_NPM_INSTALL_TIMEOUT = 600
#: Bound the short-lived ``docker`` control calls (run/inspect/rm).
_DOCKER_CALL_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Slug validation + host-path containment (mirrors consult_sandbox's posture)
# ---------------------------------------------------------------------------


def _validate_project_slug(slug: str) -> None:
    """Reject any non-canonical project slug BEFORE it is composed into a ``-v`` bind source.

    ``pathlib`` does NOT normalize ``..``, so an unvalidated slug of ``..`` would compose the bind
    SOURCE ``/opt/projects/..`` → docker would mount ALL of ``/opt`` into the sandbox, defeating the
    isolation guarantee. Reuses the ICC-canonical :data:`_PROJECT_SLUG_RE` (DRY) — identical to
    :func:`backend.services.consult_sandbox._validate_project_slug`.
    """
    if not isinstance(slug, str) or not _PROJECT_SLUG_RE.match(slug):
        raise ValueError(f"vizual sandbox: refusing unsafe project slug {slug!r}")


def _resolve_frontend_path(slug: str, frontend_path: Optional[str]) -> Path:
    """Resolve + containment-assert the host ``frontend/`` dir for ``slug``.

    The real API is slug-based (``/opt/projects/<slug>/frontend``); ``frontend_path`` is an explicit
    override for tests/atypical layouts. Belt-and-suspenders (independent of :func:`_validate_project_slug`):
    the RESOLVED (symlink-followed) path must stay strictly UNDER :data:`PROJECTS_ROOT`, so even a symlink
    or a future layout change can never point the rw bind mount outside the projects tree.
    """
    fe = Path(frontend_path) if frontend_path else (PROJECTS_ROOT / slug / "frontend")
    real = Path(os.path.realpath(fe))
    if not str(real).startswith(str(Path(os.path.realpath(PROJECTS_ROOT))) + os.sep):
        raise ValueError(f"vizual sandbox: refusing frontend path {fe} — resolves outside {PROJECTS_ROOT}")
    if not real.is_dir():
        raise FileNotFoundError(f"vizual sandbox: frontend dir not found: {real}")
    return real


# ---------------------------------------------------------------------------
# Naming + Traefik labels
# ---------------------------------------------------------------------------


def container_name(slug: str) -> str:
    """The sandbox container / Traefik router id for ``slug`` (``vizual-<slug>``)."""
    return f"{_CONTAINER_PREFIX}{slug}"


def public_host(slug: str) -> str:
    """The public vhost (``vizual-<slug>.isnex.eu``) Traefik routes to the sandbox."""
    return f"{_CONTAINER_PREFIX}{slug}.{UAT_DOMAIN_SUFFIX}"


def public_url(slug: str) -> str:
    """The public URL of the sandbox (``https://vizual-<slug>.isnex.eu``)."""
    return f"{_PUBLIC_SCHEME}://{public_host(slug)}"


def _traefik_labels(slug: str) -> list[str]:
    """The Traefik labels routing ``Host(vizual-<slug>.isnex.eu)`` → the internal Vite port.

    Exact pattern of :func:`backend.services.uat_provisioner.frontend_traefik_labels` (cleartext ``web``
    entrypoint on ``nex-proxy-net``; TLS is terminated upstream). No websocket label is needed — Traefik
    forwards the HMR WebSocket upgrade for the matched router by default.
    """
    name = container_name(slug)
    host = public_host(slug)
    return [
        "traefik.enable=true",
        f"traefik.docker.network={PROXY_NETWORK}",
        f"traefik.http.routers.{name}.rule=Host(`{host}`)",
        f"traefik.http.routers.{name}.entrypoints=web",
        f"traefik.http.services.{name}.loadbalancer.server.port={VITE_INTERNAL_PORT}",
    ]


# ---------------------------------------------------------------------------
# Vite override config (the HMR-through-Traefik solution)
# ---------------------------------------------------------------------------


def _render_override_config() -> str:
    """The tiny Vite config override that makes HMR work through the Traefik proxy.

    It **extends the project's own ``vite.config.ts``** (``mergeConfig``) so react/tailwind/aliases stay
    intact — the manager sees the REAL app, not a stripped shell — and layers on ONLY the reverse-proxy
    server settings: ``server.hmr.{host,clientPort,protocol}`` (points the browser's HMR WebSocket at the
    PUBLIC origin, without which live-reload silently no-ops behind a proxy) + ``server.allowedHosts`` (Vite
    6 rejects an unknown ``Host`` header otherwise). The public host + HMR transport arrive via env so the
    same file serves any slug. ``await base`` transparently handles both object- and function-form project
    configs. Written next to the project config at ``/app`` so its ``./vite.config`` import + node_modules +
    ``__dirname`` alias all resolve.
    """
    return (
        'import { defineConfig, mergeConfig } from "vite";\n'
        'import baseConfigExport from "./vite.config.ts";\n'
        "\n"
        'const publicHost = process.env.VIZUAL_PUBLIC_HOST ?? "localhost";\n'
        'const hmrProtocol = process.env.VIZUAL_HMR_PROTOCOL ?? "wss";\n'
        'const hmrClientPort = Number(process.env.VIZUAL_HMR_CLIENT_PORT ?? "443");\n'
        "\n"
        "export default defineConfig(async (env) => {\n"
        "  const base =\n"
        '    typeof baseConfigExport === "function"\n'
        "      ? await baseConfigExport(env)\n"
        "      : await baseConfigExport;\n"
        "  return mergeConfig(base, {\n"
        "    server: {\n"
        '      host: "0.0.0.0",\n'
        f"      port: {VITE_INTERNAL_PORT},\n"
        "      strictPort: true,\n"
        "      allowedHosts: [publicHost],\n"
        "      hmr: { host: publicHost, clientPort: hmrClientPort, protocol: hmrProtocol },\n"
        "    },\n"
        "  });\n"
        "});\n"
    )


def _git_exclude_override(frontend_host_path: Path) -> None:
    """Best-effort: keep the generated override out of the project's git via ``.git/info/exclude``.

    Uses the LOCAL, uncommitted exclude file (not the tracked ``.gitignore``) so the AI's ``vizual`` round
    can ``git add -A`` its real FE edits without ever staging the sandbox's transient config, and no tracked
    project file is mutated. The git repo root is the PROJECT dir (``frontend/`` is a subdir), so the exclude
    pattern is path-qualified. Never raises — a missing/other-layout repo just skips the guard (teardown
    still deletes the file).
    """
    try:
        info_dir = frontend_host_path.parent / ".git" / "info"
        if not info_dir.is_dir():
            return
        exclude = info_dir / "exclude"
        pattern = f"frontend/{_OVERRIDE_CONTAINER_NAME}"
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if pattern not in existing.split():
            with exclude.open("a", encoding="utf-8") as fh:
                fh.write(f"\n# nex-studio-visual vizual sandbox (transient)\n{pattern}\n")
    except OSError:
        pass


def _write_override_config(frontend_host_path: Path) -> Path:
    """Write the override config as a REAL file at the frontend root and return its path.

    Idempotent (content is fixed; overwrite is safe). Git-excluded so an interleaving AI commit never
    stages it; removed on :func:`teardown`.
    """
    path = frontend_host_path / _OVERRIDE_CONTAINER_NAME
    path.write_text(_render_override_config(), encoding="utf-8")
    _git_exclude_override(frontend_host_path)
    return path


# ---------------------------------------------------------------------------
# docker argv (the mounts ARE the isolation guarantee) + container-state helpers
# ---------------------------------------------------------------------------


def build_run_argv(*, slug: str, frontend_host_path: Path) -> list[str]:
    """Compose the EXACT ``docker run`` argv for the sandbox (every option is load-bearing).

    The ONLY bind mount is the project ``frontend/`` (rw — HMR must see host edits); the override config
    lives INSIDE it as a real file (see :data:`_OVERRIDE_CONTAINER_NAME`), so there is no second mount.
    Deliberately ABSENT (the negative half of the isolation guarantee — asserted by the tests): NO
    ``/var/run/docker.sock``, NO ``/opt/customers``, NO ``/opt/uat``, NO ``/opt/infra``, NO credentials
    store, NO knowledge mount, NO extra network. The sandbox can reach only ``nex-proxy-net``.
    """
    name = container_name(slug)
    host = public_host(slug)
    argv: list[str] = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        # Unprivileged, and == the host owner of the bind-mounted files (never root).
        "--user",
        SANDBOX_USER,
        # The ONLY network — the Traefik bus. No default bridge, no host networking.
        "--network",
        PROXY_NETWORK,
    ]
    for label in _traefik_labels(slug):
        argv += ["--label", label]
    for scratch in _VITE_SCRATCH_DIRS:
        # Writable RAM scratch shadowing Vite's node_modules cache/temp — keeps the host node_modules
        # pristine AND immune to pre-existing root-owned scratch dirs (see _VITE_SCRATCH_DIRS).
        argv += ["--tmpfs", f"{scratch}:rw,mode=1777"]
    argv += [
        # Public origin + HMR transport for the override config.
        "-e",
        f"VIZUAL_PUBLIC_HOST={host}",
        "-e",
        f"VIZUAL_HMR_PROTOCOL={_HMR_PROTOCOL}",
        "-e",
        f"VIZUAL_HMR_CLIENT_PORT={_HMR_CLIENT_PORT}",
        # A writable HOME for numeric --user (node user's /home/node is 1000-owned, but be explicit).
        "-e",
        "HOME=/tmp",
        # The ONE bind — HMR must see host edits crossing the mount. The override config already lives
        # inside it at /app/<_OVERRIDE_CONTAINER_NAME> (a real file, not a second mount).
        "-v",
        f"{frontend_host_path}:/app",
        "-w",
        "/app",
        NODE_IMAGE,
        "sh",
        "-c",
        f"npm run dev -- --config {_OVERRIDE_CONTAINER_NAME} --host 0.0.0.0 --port {VITE_INTERNAL_PORT}",
    ]
    return argv


def _inspect_state(name: str) -> dict[str, bool]:
    """``{"exists": bool, "running": bool}`` for container ``name`` (never raises)."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return {"exists": False, "running": False}
    if proc.returncode != 0:
        return {"exists": False, "running": False}  # "No such object"
    return {"exists": True, "running": proc.stdout.strip() == "true"}


def _force_remove(name: str) -> None:
    """``docker rm -f <name>`` — idempotent, never raises (a missing container is a no-op success)."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _ensure_node_modules(frontend_host_path: Path, *, slug: str) -> None:
    """Ensure ``frontend/node_modules`` exists (scaffold normally installs it); one-shot ``npm install``.

    Runs in the SAME isolated posture as the dev-server (only the frontend bind, only nex-proxy-net) so
    the install can never touch anything but the project.
    """
    if (frontend_host_path / "node_modules").is_dir():
        return
    logger.info("vizual sandbox: node_modules missing for %s — running one-shot npm install", slug)
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            SANDBOX_USER,
            "--network",
            PROXY_NETWORK,
            "-e",
            "HOME=/tmp",
            "-v",
            f"{frontend_host_path}:/app",
            "-w",
            "/app",
            NODE_IMAGE,
            "npm",
            "install",
        ],
        capture_output=True,
        text=True,
        timeout=_NPM_INSTALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"vizual sandbox: npm install failed for {slug}: {proc.stderr.strip()[-500:]}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def spin_up(slug: str, frontend_path: Optional[str] = None) -> str:
    """Start (idempotently) an isolated Vite dev-server sandbox for ``slug`` and return its public URL.

    Starts container ``vizual-<slug>`` for ``/opt/projects/<slug>/frontend`` (or the explicit
    ``frontend_path``), on ``nex-proxy-net``, with Traefik labels routing
    ``Host(vizual-<slug>.isnex.eu)`` → the internal Vite port. If a container of that name is already
    RUNNING, returns the URL without recreating; a leftover STOPPED container is removed and recreated.
    Returns ``https://vizual-<slug>.isnex.eu``.

    Raises:
        ValueError: unsafe slug / frontend path escaping the projects root.
        FileNotFoundError: the frontend dir does not exist.
        RuntimeError: node_modules install failed, or the container died immediately after start.
    """
    _validate_project_slug(slug)
    frontend_host_path = _resolve_frontend_path(slug, frontend_path)
    name = container_name(slug)

    state = _inspect_state(name)
    if state["running"]:
        logger.info("vizual sandbox: %s already running — reusing", name)
        return public_url(slug)
    if state["exists"]:
        _force_remove(name)  # a stopped/crashed leftover — recreate clean

    _ensure_node_modules(frontend_host_path, slug=slug)
    _write_override_config(frontend_host_path)

    argv = build_run_argv(slug=slug, frontend_host_path=frontend_host_path)
    logger.info("vizual sandbox: starting %s for %s", name, frontend_host_path)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"vizual sandbox: docker run failed for {slug}: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"vizual sandbox: docker run failed for {slug}: {proc.stderr.strip()[-500:]}")

    # ``-d`` returns as soon as the container is created; verify the entrypoint did not crash instantly
    # (a bad node_modules / config surfaces here rather than as a silent dead route). Deeper readiness
    # (Vite finished booting) is the caller's poll — the cockpit shows a "sandbox is starting" state.
    if not _inspect_state(name)["running"]:
        raise RuntimeError(f"vizual sandbox: container {name} exited immediately after start")
    return public_url(slug)


def teardown(slug: str, frontend_path: Optional[str] = None) -> None:
    """``docker rm -f vizual-<slug>`` + delete the generated override — leaves the project tree clean.

    No error if the container/file is absent. Never raises. ``frontend_path`` mirrors :func:`spin_up`'s
    escape hatch; the real API resolves the default ``/opt/projects/<slug>/frontend`` layout.
    """
    _validate_project_slug(slug)
    _force_remove(container_name(slug))
    try:
        fe = Path(frontend_path) if frontend_path else (PROJECTS_ROOT / slug / "frontend")
        (fe / _OVERRIDE_CONTAINER_NAME).unlink(missing_ok=True)
    except OSError:
        pass


def status(slug: str) -> dict:
    """Return ``{"running": bool, "url": str | None}`` for the sandbox of ``slug``."""
    _validate_project_slug(slug)
    running = _inspect_state(container_name(slug))["running"]
    return {"running": running, "url": public_url(slug) if running else None}
