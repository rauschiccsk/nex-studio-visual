"""Isolated LIVE-PREVIEW Vite dev-server sandbox for the ``vizual`` stage (CR-1 sub-task 2).

The ``vizual`` stage (``docs/specs/cr1-faza2-skeleton-spec.md`` §3.C) shows a project's frontend
**live** in the cockpit while the AI edits its source: a manager watches the app, asks for a change
in chat, the AI applies it, and it hot-reloads through Vite HMR in <1 s with **no rebuild**. This
module is the net-new "living dev-server sandbox" that makes that possible.

Proven mechanism (PoC passed 2026-07-12): ``node:20`` with the project ``frontend/`` bind-mounted at
``/app`` running ``vite`` serves the FE and reflects a HOST source edit in ~0.03 s — Vite's file
watcher crosses the bind mount natively (no ``CHOKIDAR_USEPOLLING`` needed). This module wraps that in
the two disciplines the real thing needs:

  * **Isolation** (the shared :mod:`backend.services.sandbox_paths` posture): the slug is validated and the
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

import hashlib
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

#: The ICC-canonical kebab-case project-slug rule, reused verbatim (DRY) — identical to the rule
#: :mod:`backend.services.sandbox_paths` guards every bind source with. Rejects ``..`` / ``/`` / empty /
#: anything non-slug BEFORE it is composed into a ``-v`` bind source.
from backend.services.project_specs import _SLUG_RE as _PROJECT_SLUG_RE

#: Traefik/infra constants — single source of truth in the UAT provisioner (DRY).
from backend.services.uat_provisioner import PROXY_NETWORK, domain_suffix

logger = logging.getLogger(__name__)

#: Host root under which every managed project lives. The nex-studio-visual backend bind-mounts this
#: IDENTITY (``/opt/projects`` → ``/opt/projects``, verified via ``docker inspect``), so a host-daemon
#: ``docker run`` resolves the same absolute path the backend sees — no container→host translation is
#: needed for this deployment (unlike the legacy nex-studio ``/opt/projects-v3`` split).
PROJECTS_ROOT = Path("/opt/projects")

#: The port Vite listens on INSIDE the sandbox (Traefik load-balances to it over ``nex-proxy-net``).
VITE_INTERNAL_PORT = 5173

#: Hodnoty, ktorými sa živý náhľad ZAPÍNA — a nič iné (ICCINT-93, ICCINT-95). Aplikácia porovnáva
#: ``import.meta.env.VITE_PREVIEW`` s celým týmto zoznamom; šablóna nového projektu to má tak
#: napísané a stráž to kontroluje proti skutočným aplikáciám na disku.
#:
#: ⚠️ Dva protichodné omyly, oba už zmerané:
#: - porovnanie s JEDINÝM slovom sa minie s hodnotou a náhľad sa vôbec nezapne — NEX Manager mal
#:   ``=== "true"``, kým sa posielala ``1``, a Manažér z toho videl prihlasovaciu stenu (ICCINT-93);
#: - PRAVDIVOSTNÁ kontrola má opačnú chybu: v JavaScripte je pravdivý aj reťazec ``"false"``, takže
#:   ``VITE_PREVIEW=false`` by náhľad ZAPLO (ICCINT-95). Zvyk vypínať slovom „false“ je pritom živý —
#:   ``VITE_LAUNCH_LIVE=false`` sa tak naozaj používa.
#:
#: Zoznam rieši oba naraz: vymenované hodnoty zapínajú, čokoľvek iné (``false``, ``0``, ``off``,
#: prázdno) nezapína.
PREVIEW_ON = ("1", "true", "yes", "on")

#: Čo pieskovisko aplikácii naozaj pošle. Musí byť z ``PREVIEW_ON``; ``true`` je z nich to jediné, ktoré
#: vyhovie aj starším aplikáciám s pravdivostnou kontrolou, aj tým s porovnaním so slovom.
PREVIEW_FLAG_VALUE = "true"

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

#: DEV-241: the label a preview carries — the fingerprint of the libraries it was started with. Vite bundles the
#: libraries once, when it starts (into the tmpfs cache above), so a preview started before the AI Agent raised or
#: added a library serves the old one for as long as it runs: NEX Knowledge 0.1.0 kept the card without its inner
#: spacing after nex-shared v0.24.0 was installed (27.10.2026). A preview whose fingerprint is not its frontend's
#: current one is built anew wherever the cockpit starts it (:func:`spin_up`) or looks at it
#: (:func:`preview_is_current`).
DEPENDENCIES_LABEL = "nex-studio.vizual.dependencies"
#: What the fingerprint is read from — the lock file names the exact libraries; package.json where there is none.
_DEPENDENCY_FILES = ("package-lock.json", "package.json")


# ---------------------------------------------------------------------------
# Slug validation + host-path containment (the sandbox_paths posture)
# ---------------------------------------------------------------------------


def _validate_project_slug(slug: str) -> None:
    """Reject any non-canonical project slug BEFORE it is composed into a ``-v`` bind source.

    ``pathlib`` does NOT normalize ``..``, so an unvalidated slug of ``..`` would compose the bind
    SOURCE ``/opt/projects/..`` → docker would mount ALL of ``/opt`` into the sandbox, defeating the
    isolation guarantee. Reuses the ICC-canonical :data:`_PROJECT_SLUG_RE` (DRY) — identical to
    :func:`backend.services.sandbox_paths.validate_project_slug`.
    """
    if not isinstance(slug, str) or not _PROJECT_SLUG_RE.match(slug):
        raise ValueError(f"vizual sandbox: refusing unsafe project slug {slug!r}")


#: DEV-162: the frontend every project has; a Web project's second one (``web``) gets a preview of its own.
MAIN_FRONTEND = "frontend"
_DIRECTORY_RE = re.compile(r"^[a-z][a-z0-9]*$")


def _validate_directory(directory: str) -> None:
    """A frontend directory is one plain word — it becomes part of a container name, a host name and a bind path."""
    if not isinstance(directory, str) or not _DIRECTORY_RE.match(directory):
        raise ValueError(f"vizual sandbox: refusing frontend directory {directory!r}")


def _resolve_frontend_path(slug: str, frontend_path: Optional[str], directory: str = MAIN_FRONTEND) -> Path:
    """Resolve + containment-assert the host ``frontend/`` dir for ``slug``.

    The real API is slug-based (``/opt/projects/<slug>/frontend``); ``frontend_path`` is an explicit
    override for tests/atypical layouts. Belt-and-suspenders (independent of :func:`_validate_project_slug`):
    the RESOLVED (symlink-followed) path must stay strictly UNDER :data:`PROJECTS_ROOT`, so even a symlink
    or a future layout change can never point the rw bind mount outside the projects tree.
    """
    _validate_directory(directory)
    fe = Path(frontend_path) if frontend_path else (PROJECTS_ROOT / slug / directory)
    real = Path(os.path.realpath(fe))
    if not str(real).startswith(str(Path(os.path.realpath(PROJECTS_ROOT))) + os.sep):
        raise ValueError(f"vizual sandbox: refusing frontend path {fe} — resolves outside {PROJECTS_ROOT}")
    if not real.is_dir():
        raise FileNotFoundError(f"vizual sandbox: frontend dir not found: {real}")
    return real


# ---------------------------------------------------------------------------
# Naming + Traefik labels
# ---------------------------------------------------------------------------


def container_name(slug: str, directory: str = MAIN_FRONTEND) -> str:
    """The sandbox container / Traefik router id — ``vizual-<slug>`` for ``frontend/``, ``vizual<dir>-<slug>`` for a
    further frontend (DEV-162: ``vizualweb-<slug>``). The directory sits in the PREFIX, never after the slug: a slug
    may end in ``-web`` itself, and ``vizual-<slug>-web`` could then be another project's main preview."""
    _validate_directory(directory)
    prefix = _CONTAINER_PREFIX if directory == MAIN_FRONTEND else f"vizual{directory}-"
    return f"{prefix}{slug}"


def public_host(slug: str, *, private: bool = False, directory: str = MAIN_FRONTEND) -> str:
    """The vhost Traefik routes to the sandbox — ``vizual-<slug>.isnex.eu``, or ``vizual-<slug>.int.isnex.eu`` for a
    project reachable only from the private network (DEV-42); a further frontend's in its own name (DEV-162)."""
    return f"{container_name(slug, directory)}.{domain_suffix(private)}"


def public_url(slug: str, *, private: bool = False, directory: str = MAIN_FRONTEND) -> str:
    """The URL of the sandbox (``https://vizual-<slug>.isnex.eu`` or its private-zone twin)."""
    return f"{_PUBLIC_SCHEME}://{public_host(slug, private=private, directory=directory)}"


def _traefik_labels(slug: str, *, private: bool = False, directory: str = MAIN_FRONTEND) -> list[str]:
    """The Traefik labels routing ``Host(vizual-<slug>.isnex.eu)`` → the internal Vite port.

    Exact pattern of :func:`backend.services.uat_provisioner.frontend_traefik_labels` (cleartext ``web``
    entrypoint on ``nex-proxy-net``; TLS is terminated upstream). No websocket label is needed — Traefik
    forwards the HMR WebSocket upgrade for the matched router by default.
    """
    name = container_name(slug, directory)
    host = public_host(slug, private=private, directory=directory)
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

    It ALSO forces ``optimizeDeps.include: ["msw", "msw/browser"]`` (v4.0.45 — the live-preview cold-start
    fix). The preview harness every vizual app ships starts MSW (a Service-Worker mock backend) BEFORE first
    render so the app runs with fixtures and no real backend. But Vite pre-bundles deps lazily: it discovers
    ``msw`` only when the code first imports it, then re-optimizes and RELOADS the page — and in that window
    the app's first request escapes the not-yet-active mock worker, hits the absent backend, and a stray
    failure bounces the preview to a login/unauthorized wall (the manager sees a login form instead of the
    app). Pre-including msw makes Vite bundle it at server STARTUP, so the mock worker is ready on the very
    first load and no request ever leaks. Centralised here so EVERY app's preview is robust regardless of
    what the AI generated (belt: the charter also has the app no-op ``onUnauthorized`` under ``VITE_PREVIEW``).
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
        '    optimizeDeps: { include: ["msw", "msw/browser"] },\n'
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
        pattern = f"{frontend_host_path.name}/{_OVERRIDE_CONTAINER_NAME}"
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


#: DEV-126: an app's „Aktualizácie" read the release notes from ``docs/specs/versions`` beside ``frontend/`` —
#: ``../docs/specs/versions`` seen from ``/app`` — the cockpit's own standard for every app. Without them the
#: preview of NEX Manager 1.3.0 stopped seven minutes after „Vizuál je pripravený" (28.09.2026) and stayed dead.
RELEASE_NOTES_DIR = Path("docs") / "specs" / "versions"
RELEASE_NOTES_IN_SANDBOX = "/docs/specs/versions"


def _release_notes_mount(frontend_host_path: Path) -> list[str]:
    """The project's release notes for the preview — read-only, only when they are a real folder in the project.

    ``--mount``, never ``-v``: a missing source is refused instead of being created as an empty root-owned folder.
    A link leading out of the project never reaches the sandbox, which serves a public address.
    """
    project = frontend_host_path.parent
    notes = project / RELEASE_NOTES_DIR
    if not notes.is_dir():
        return []
    resolved = notes.resolve()
    if not resolved.is_relative_to(project.resolve()):
        return []
    return ["--mount", f"type=bind,source={resolved},target={RELEASE_NOTES_IN_SANDBOX},readonly"]


def dependencies_fingerprint(frontend_host_path: Path) -> str:
    """DEV-241: the libraries a preview of ``frontend_host_path`` bundles — 16 hex of the SHA-256 of its
    ``package-lock.json`` (``package.json`` without one); ``""`` when the frontend has neither."""
    for name in _DEPENDENCY_FILES:
        try:
            return hashlib.sha256((frontend_host_path / name).read_bytes()).hexdigest()[:16]
        except OSError:
            continue
    return ""


def build_run_argv(
    *,
    slug: str,
    frontend_host_path: Path,
    private: bool = False,
    directory: str = MAIN_FRONTEND,
    dependencies: Optional[str] = None,
) -> list[str]:
    """Compose the EXACT ``docker run`` argv for the sandbox (every option is load-bearing).

    The bind mounts are the project ``frontend/`` (rw — HMR must see host edits) and, read-only, its release
    notes (:func:`_release_notes_mount`, DEV-126); the override config lives INSIDE ``frontend/`` as a real file
    (see :data:`_OVERRIDE_CONTAINER_NAME`), so it needs no mount of its own.
    Deliberately ABSENT (the negative half of the isolation guarantee — asserted by the tests): NO
    ``/var/run/docker.sock``, NO ``/opt/customers``, NO ``/opt/uat``, NO ``/opt/infra``, NO credentials
    store, NO knowledge mount, NO extra network. The sandbox can reach only ``nex-proxy-net``.
    """
    name = container_name(slug, directory)
    host = public_host(slug, private=private, directory=directory)
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
    for label in _traefik_labels(slug, private=private, directory=directory):
        argv += ["--label", label]
    # DEV-241: the libraries this preview bundles — compared with its frontend's by spin_up / preview_is_current.
    if dependencies is None:
        dependencies = dependencies_fingerprint(frontend_host_path)
    argv += ["--label", f"{DEPENDENCIES_LABEL}={dependencies}"]
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
        # Preview mode (v4.0.22): Vite auto-exposes VITE_-prefixed vars to import.meta.env, so the FE's
        # preview entry (MSW mock /session + representative-data handlers) activates → the live FE renders
        # in the sandbox WITHOUT a backend/login. This is what makes the FAITHFUL live-FE Vizuál possible
        # (replacing the decoupled self-contained mockup): the Manažér approves the REAL nex-shared screens.
        #
        # ⚠️ HODNOTA JE ``true``, A NIE ``1`` — ICCINT-93. Dovtedy sa posielala ``1`` a nikde nebolo
        # napísané, akú hodnotu má aplikácia očakávať, takže si to každý projekt tipol. Štyri projekty tipli
        # pravdivostnú kontrolu a fungovalo im to; NEX Manager tipol ``=== "true"`` a náhľad sa mu vôbec
        # nezapol. Zmerané 09.09.2026 — a Manažér z toho videl PRIHLASOVACIU STENU a hlásil „nepoznám
        # prihlasovacie údaje“, teda niečo úplne iné, než čo bolo príčinou.
        #
        # ``true`` vyhovie OBOM zvyklostiam naraz: je pravdivé aj sa rovná slovu ``"true"``. Neexistuje
        # rozumná kontrola, ktorá by prešla pri ``1`` a zlyhala pri ``true``. Odstraňuje to celú triedu
        # chyby namiesto toho, aby sa na správnu hodnotu spoliehalo.
        #
        # Hodnota sa skladá z ``PREVIEW_ON`` (nižšie) — aplikácie porovnávajú s tým istým zoznamom, takže
        # dohoda medzi pieskoviskom a aplikáciou stojí na jednom mieste, nie na dvoch kópiách.
        "-e",
        f"VITE_PREVIEW={PREVIEW_FLAG_VALUE}",
        # A writable HOME for numeric --user (node user's /home/node is 1000-owned, but be explicit).
        "-e",
        "HOME=/tmp",
        # The ONE writable bind — HMR must see host edits crossing the mount. The override config already lives
        # inside it at /app/<_OVERRIDE_CONTAINER_NAME> (a real file, not a second mount). The release notes
        # follow read-only (DEV-126).
        "-v",
        f"{frontend_host_path}:/app",
        *_release_notes_mount(frontend_host_path),
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


def _running_public_host(name: str) -> Optional[str]:
    """The host a running sandbox was started for (its ``VIZUAL_PUBLIC_HOST``), or ``None`` (never raises)."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("VIZUAL_PUBLIC_HOST="):
            return line.split("=", 1)[1].strip()
    return None


def _running_dependencies(name: str) -> Optional[str]:
    """DEV-241: the fingerprint a preview was started with (``""`` for one started before the label existed), or
    ``None`` when docker cannot be asked (never raises)."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", f'{{{{index .Config.Labels "{DEPENDENCIES_LABEL}"}}}}', name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


#: DEV-241: one spin-up of a preview at a time — the round, its end, the state check of every open Riadiace centrum
#: and „Spustiť náhľad znova“ may all find the same preview on old libraries; the first builds it anew, the others
#: then find it current. Without it two of them each removed and started the container.
_SPIN_LOCKS: dict[str, threading.Lock] = {}
_SPIN_LOCKS_GUARD = threading.Lock()


def _spin_lock(name: str) -> threading.Lock:
    with _SPIN_LOCKS_GUARD:
        return _SPIN_LOCKS.setdefault(name, threading.Lock())


def _force_remove(name: str) -> None:
    """``docker rm -f -v <name>`` — with its unnamed volumes (DEV-172); idempotent, never raises (a missing container is
    a no-op success)."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", "-v", name],
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


def spin_up(
    slug: str, frontend_path: Optional[str] = None, *, private: bool = False, directory: str = MAIN_FRONTEND
) -> str:
    """Start (idempotently) an isolated Vite dev-server sandbox for ``slug`` and return its public URL.

    Starts container ``vizual-<slug>`` for ``/opt/projects/<slug>/frontend`` (or the explicit
    ``frontend_path``), on ``nex-proxy-net``, with Traefik labels routing
    ``Host(vizual-<slug>.isnex.eu)`` → the internal Vite port. If a container of that name is already
    RUNNING under the expected name and on the libraries its frontend has now, returns the URL without recreating; a
    leftover STOPPED container — a running one under the other zone's name, after the project's private-network
    option changed (DEV-42), or one started with other libraries (DEV-241) — is removed and recreated. One spin-up
    of a preview runs at a time. Returns ``https://vizual-<slug>.isnex.eu`` (``.int.isnex.eu`` when ``private``).

    Raises:
        ValueError: unsafe slug / frontend path escaping the projects root.
        FileNotFoundError: the frontend dir does not exist.
        RuntimeError: node_modules install failed, or the container died immediately after start.
    """
    _validate_project_slug(slug)
    frontend_host_path = _resolve_frontend_path(slug, frontend_path, directory)
    name = container_name(slug, directory)

    host = public_host(slug, private=private, directory=directory)
    with _spin_lock(name):
        return _spin_up_locked(slug, name, host, frontend_host_path, private=private, directory=directory)


def _spin_up_locked(slug: str, name: str, host: str, frontend_host_path: Path, *, private: bool, directory: str) -> str:
    state = _inspect_state(name)
    if state["running"] and _running_public_host(name) == host:
        if _running_dependencies(name) == dependencies_fingerprint(frontend_host_path):
            logger.info("vizual sandbox: %s already running — reusing", name)
            return public_url(slug, private=private, directory=directory)
        logger.info("vizual sandbox: %s runs on other libraries than its frontend has now — building it anew", name)
    if state["exists"]:
        # A stopped/crashed leftover, one still answering under the other zone's name (DEV-42: a preview of a
        # private project must not keep its public name), or one bundling old libraries (DEV-241) — recreate clean.
        _force_remove(name)

    _ensure_node_modules(frontend_host_path, slug=slug)
    _write_override_config(frontend_host_path)

    # After _ensure_node_modules: an install may rewrite the lock file, and the label must name what Vite bundles.
    argv = build_run_argv(
        slug=slug,
        frontend_host_path=frontend_host_path,
        private=private,
        directory=directory,
        dependencies=dependencies_fingerprint(frontend_host_path),
    )
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
    return public_url(slug, private=private, directory=directory)


def teardown(slug: str, frontend_path: Optional[str] = None, *, directory: str = MAIN_FRONTEND) -> None:
    """``docker rm -f vizual-<slug>`` + delete the generated override — leaves the project tree clean.

    No error if the container/file is absent. Never raises. ``frontend_path`` mirrors :func:`spin_up`'s
    escape hatch; the real API resolves the default ``/opt/projects/<slug>/frontend`` layout.
    """
    _validate_project_slug(slug)
    _force_remove(container_name(slug, directory))
    try:
        fe = Path(frontend_path) if frontend_path else (PROJECTS_ROOT / slug / directory)
        (fe / _OVERRIDE_CONTAINER_NAME).unlink(missing_ok=True)
    except OSError:
        pass


#: DEV-127: the words a stopped preview left — the last line of its record that names an error, for the Manažér.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ERROR_LINE = re.compile(r"(?i)\b(error|failed|cannot|neexistuje|nenašiel)\b")
_REASON_MAX = 300


def _stopped_reason(name: str) -> Optional[str]:
    """The last error line of a stopped preview's record (never raises; ``None`` when nothing names an error)."""
    try:
        proc = subprocess.run(
            ["docker", "logs", "--tail", "80", name],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [_ANSI.sub("", line).strip() for line in (proc.stdout + "\n" + proc.stderr).splitlines()]
    errors = [line for line in lines if line and _ERROR_LINE.search(line) and not line.startswith("at ")]
    return errors[-1][:_REASON_MAX] if errors else None


def preview_state(slug: str, *, private: bool = False, directory: str = MAIN_FRONTEND) -> dict:
    """DEV-127: ``{"state": "running" | "stopped" | "missing", "url": str | None, "reason": str | None}``.

    ``stopped`` carries the last error line the preview left, so the cockpit can say WHY it is not running —
    on 28.09.2026 the preview of NEX Manager 1.3.0 died seven minutes after „Vizuál je pripravený" and the
    cockpit kept offering „Schváliť vizuál" for seventeen days.
    """
    _validate_project_slug(slug)
    name = container_name(slug, directory)
    seen = _inspect_state(name)
    if seen["running"]:
        return {"state": "running", "url": public_url(slug, private=private, directory=directory), "reason": None}
    if seen["exists"]:
        return {"state": "stopped", "url": None, "reason": _stopped_reason(name)}
    return {"state": "missing", "url": None, "reason": None}


def preview_is_current(slug: str, *, directory: str = MAIN_FRONTEND) -> bool:
    """DEV-241: does the preview of ``slug`` bundle the libraries its frontend has now?

    ``True`` when it cannot be told — no such frontend, or docker cannot be asked: a preview is never rebuilt on a
    guess. A preview started before the fingerprint existed has none and is not current.
    """
    _validate_project_slug(slug)
    try:
        frontend_host_path = _resolve_frontend_path(slug, None, directory)
    except (ValueError, FileNotFoundError):
        return True
    started_with = _running_dependencies(container_name(slug, directory))
    if started_with is None:
        return True
    return started_with == dependencies_fingerprint(frontend_host_path)


def status(slug: str, *, private: bool = False, directory: str = MAIN_FRONTEND) -> dict:
    """Return ``{"running": bool, "url": str | None}`` for the sandbox of ``slug`` (its ``directory`` frontend)."""
    _validate_project_slug(slug)
    running = _inspect_state(container_name(slug, directory))["running"]
    return {"running": running, "url": public_url(slug, private=private, directory=directory) if running else None}
