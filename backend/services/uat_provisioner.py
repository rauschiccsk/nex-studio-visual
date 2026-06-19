"""Importable UAT provisioner service (v0.9.0 Phase 2, CR-1 + CR-2 + CR-3).

Renders a UAT ``docker-compose.yml`` + ``.env`` + dirs for a project so the engine
(Phase 3) can provision a UAT **in-process** — and the manual ``scripts/uat-deploy.py``
CLI delegates here too. This module is **self-contained** (no import of ``scripts/_uat_lib``)
so it imports cleanly from the async orchestrator; the sync :func:`provision_uat` is meant to
be wrapped via ``asyncio.to_thread`` by an async caller.

Design (per ``docs/specs/versions/v0.9.0/spec/phase-2-provisioner.md``):

CR-1 — Generalize the compose. Pass through **ALL** source-project services
    (``/opt/projects/<slug>/docker-compose.yml``): qdrant, redis, workers … preserving their
    image/build, environment, volumes, healthchecks, ``extra_hosts`` (Ollama
    ``host.docker.internal:host-gateway``) and any networks they declare (e.g. the external
    ``nex-network``). Routing is via **Traefik** (Phase-1 infra), NOT host ports — the FE +
    BE services join the external ``nex-proxy-net`` and carry the exact Traefik labels copied
    from the live ledger migration.

CR-2 — Importable service. :func:`provision_uat` renders compose + ``.env`` + creates dirs
    and **does NOT build/up** (that is the engine's ``_run_uat_deploy`` in Phase 3). Secrets
    are generated synthetically (``${VAR}`` → ``__UAT_SYNTHETIC__`` placeholder, ``*_PASSWORD``
    / ``*_SECRET`` / ``*_KEY`` / ``*_TOKEN`` → random), the ``.env`` is ``chmod 600`` and is
    never logged/printed. The nginx-vhost rendering is dropped (Traefik replaces it).

CR-3 — :func:`derive_uat_slug` = ``project.slug`` with a leading ``nex-`` stripped. The
    persistent write path (``set_uat_slug``) lives in :mod:`backend.services.project`.

Isolation: the rendered compose sets a top-level ``name: uat-<slug>`` so Docker namespaces
every otherwise-unnamed network/volume with that project prefix — the UAT cannot clobber the
source project's running containers/volumes. Container names + built-image tags are renamed to
``uat-<slug>-<service>``; external networks (``external: true``) pass through verbatim.
"""

from __future__ import annotations

import base64
import copy
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jinja2
import yaml

# Repo root = .../nex-studio (this file is backend/services/uat_provisioner.py).
NEX_STUDIO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = NEX_STUDIO_ROOT / "templates"

UAT_ROOT = Path("/opt/uat")
PROJECTS_ROOT = Path("/opt/projects")

# Phase-1 infra: the external Traefik docker network + the public UAT domain suffix.
PROXY_NETWORK = "nex-proxy-net"
UAT_DOMAIN_SUFFIX = "isnex.eu"

# Traefik router priorities — the BE ``/api`` split wins over the FE catch-all.
FE_ROUTER_PRIORITY = 10
BE_ROUTER_PRIORITY = 20

# UAT-internal postgres hostname/port the backend connection vars are rewritten to.
UAT_DB_PORT = "5432"

# Synthetic secret detection (case-insensitive suffix match) + the ${VAR} placeholder.
SECRET_SUFFIXES = ("_password", "_secret", "_key", "_token")
USER_SECRET_PLACEHOLDER = "__UAT_SYNTHETIC__"

# DB connection vars — explicit whitelist (pattern matching would catch false positives like
# ``PRODUCTION_DB_BACKUP_URL``). Rewritten to the UAT db service + shared synthetic password.
DB_CONNECTION_VARS = {
    "DATABASE_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
}

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# Slug validation + derivation (CR-3)
# ---------------------------------------------------------------------------


def validate_uat_slug(slug: str) -> None:
    """Raise ``ValueError`` if ``slug`` is not a valid UAT slug.

    Mirrors ``scripts/_uat_lib.validate_slug`` (lowercase ASCII letters/digits/hyphens, no
    leading hyphen/dot/slash) — duplicated here so the backend module stays self-contained.
    """
    if not slug:
        raise ValueError("slug must not be empty")
    if slug != slug.lower():
        raise ValueError(f"slug must be lowercase: {slug!r}")
    if "/" in slug:
        raise ValueError(f"slug contains slash (invalid char): {slug!r}")
    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"slug invalid char (allowed: a-z, 0-9, hyphen, no leading hyphen): {slug!r}")


def derive_uat_slug(project: Any) -> str:
    """Derive a UAT slug from a project (CR-3): ``project.slug`` with a single leading ``nex-`` stripped.

    ``nex-ledger`` → ``ledger``, ``nex-inbox`` → ``inbox``, ``nex-asistent`` → ``asistent``;
    a slug without the prefix is returned unchanged (``demo`` → ``demo``). Accepts either a
    ``Project`` (anything with a ``.slug``) or a raw slug string. The result is validated.
    """
    slug = getattr(project, "slug", project)
    if not isinstance(slug, str):
        raise TypeError(f"derive_uat_slug expects a Project or str, got {type(project)!r}")
    derived = slug.removeprefix("nex-")
    validate_uat_slug(derived)
    return derived


# ---------------------------------------------------------------------------
# Source compose loading + role detection (generalized, CR-1)
# ---------------------------------------------------------------------------


def load_source_compose(source_project_path: Path) -> dict[str, Any]:
    """Load + parse the source ``docker-compose.yml``. Raises ``FileNotFoundError`` if absent,
    ``ValueError`` on parse error or a structurally-invalid (no ``services``) compose."""
    compose_path = source_project_path / "docker-compose.yml"
    if not compose_path.is_file():
        raise FileNotFoundError(f"source docker-compose.yml not found: {compose_path}")
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"source docker-compose.yml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict) or not data["services"]:
        raise ValueError(f"source docker-compose.yml has no services: {compose_path}")
    return data


def _service_build_text(svc: dict[str, Any]) -> str:
    """Lowercased ``build.context`` + ``build.dockerfile`` (or a string build) for role heuristics."""
    build = svc.get("build")
    if isinstance(build, str):
        return build.lower()
    if isinstance(build, dict):
        return f"{build.get('context', '')} {build.get('dockerfile', '')}".lower()
    return ""


def _find_backend(services: dict[str, Any]) -> Optional[str]:
    """The backend service name — by convention ``backend``, else a build pointing at ``backend``."""
    if "backend" in services:
        return "backend"
    for name, svc in services.items():
        if "backend" in name.lower() or "backend" in _service_build_text(svc):
            return name
    return None


def _find_frontend(services: dict[str, Any]) -> Optional[str]:
    """The frontend service name — by convention ``frontend``, else a build pointing at ``frontend``
    or an nginx-served image."""
    if "frontend" in services:
        return "frontend"
    for name, svc in services.items():
        if "frontend" in name.lower() or "frontend" in _service_build_text(svc):
            return name
    for name, svc in services.items():
        image = str(svc.get("image", "")).lower()
        if image.startswith("nginx") or "nginx" in image:
            return name
    return None


def _find_db(services: dict[str, Any]) -> Optional[str]:
    """The database service name — by convention ``db``/``postgres``/``database``, else a
    ``postgres:*`` image."""
    for candidate in ("db", "postgres", "database"):
        if candidate in services:
            return candidate
    for name, svc in services.items():
        if str(svc.get("image", "")).lower().startswith("postgres"):
            return name
    return None


def identify_service_roles(services: dict[str, Any]) -> dict[str, Optional[str]]:
    """Map FE/BE/DB roles to source service names (``None`` when a role is absent).

    Detection is by **role/convention**, not by assuming the service is literally named
    ``frontend``/``backend`` (per CR-1) — so an N-service stack (qdrant, redis, workers …)
    routes the right two services through Traefik.
    """
    return {
        "backend": _find_backend(services),
        "frontend": _find_frontend(services),
        "db": _find_db(services),
    }


def _container_port(port_entry: Any) -> Optional[int]:
    """Container-side port from a compose ``ports`` entry (short ``H:C`` / ``IP:H:C`` /
    ``C/tcp`` or long-form ``{target: C}``); ``None`` on parse failure."""
    if isinstance(port_entry, dict):
        target = port_entry.get("target")
        if isinstance(target, int):
            return target
        if isinstance(target, str) and target.isdigit():
            return int(target)
        return None
    if isinstance(port_entry, int):
        return port_entry
    if not isinstance(port_entry, str):
        return None
    last = port_entry.split("/", 1)[0].rsplit(":", 1)[-1].strip()
    return int(last) if last.isdigit() else None


# Extract the URL path from an `http(s)://host[:port]/PATH` occurrence.
_HEALTH_URL_PATH = re.compile(r"https?://[^/\s\"']+(/[^\s\"']*)")


def _extract_url_path(text: str) -> Optional[str]:
    """Path component of the first http(s) URL in ``text`` (``/api/v1/health`` …), else ``None``."""
    m = _HEALTH_URL_PATH.search(text)
    return m.group(1) if m else None


def extract_health_path(healthcheck_test: Any) -> Optional[str]:
    """The URL path from a compose ``healthcheck.test`` (list-of-args or string). ``None`` if no URL."""
    if not healthcheck_test:
        return None
    text = " ".join(str(x) for x in healthcheck_test) if isinstance(healthcheck_test, list) else str(healthcheck_test)
    return _extract_url_path(text)


def detect_dockerfile_health_path(dockerfile_path: Path) -> Optional[str]:
    """The URL path of a backend Dockerfile ``HEALTHCHECK`` instruction (some projects, e.g. nex-inbox,
    declare the probe ONLY there). ``None`` when the file/instruction/URL is absent."""
    if not dockerfile_path.is_file():
        return None
    text = dockerfile_path.read_text(encoding="utf-8")
    m = re.search(r"\bHEALTHCHECK\b", text, re.IGNORECASE)
    if m is None:
        return None
    return _extract_url_path(text[m.start() :])


def resolve_be_health_path(be_svc: Optional[dict[str, Any]], project_path: Path) -> str:
    """Resolve the backend health endpoint PATH: source compose ``healthcheck.test`` URL → backend
    Dockerfile ``HEALTHCHECK`` URL → ``/health``. Used by the CLI's wait-healthy probe."""
    if be_svc:
        healthcheck = be_svc.get("healthcheck") or {}
        from_compose = extract_health_path(healthcheck.get("test"))
        if from_compose:
            return from_compose
        build = be_svc.get("build")
        if isinstance(build, dict):
            ctx_abs = _abs_build_context(str(build.get("context", ".")), project_path)
            dockerfile = str(build.get("dockerfile", "Dockerfile"))
            from_dockerfile = detect_dockerfile_health_path(Path(ctx_abs) / dockerfile)
            if from_dockerfile:
                return from_dockerfile
    return "/health"


def detect_internal_port(svc: dict[str, Any], default: int) -> int:
    """The container-side port a service listens on — first ``ports`` mapping, else ``expose[0]``,
    else ``default`` (FE 80 / BE 8000)."""
    for mapping in svc.get("ports", []) or []:
        port = _container_port(mapping)
        if port is not None:
            return port
    for exposed in svc.get("expose", []) or []:
        if isinstance(exposed, int):
            return exposed
        if isinstance(exposed, str) and exposed.isdigit():
            return int(exposed)
    return default


# ---------------------------------------------------------------------------
# .env synthesis (synthetic secrets, §4)
# ---------------------------------------------------------------------------


def _synthetic_secret(key: str) -> str:
    """Synthetic value for a secret env var (format heuristic).

    ``*_KEY`` → standard-alphabet base64 of 32 random bytes (decodes to 32 bytes for strict
    AES-key readers AND urlsafe decoders); all other secret suffixes → ``token_hex(32)``.
    """
    if key.lower().endswith("_key"):
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return secrets.token_hex(32)


def _is_var_expansion(value: Any) -> bool:
    """True for ``${VAR}`` / ``${VAR:-default}`` env-var expansion notation."""
    return isinstance(value, str) and value.startswith("${") and value.endswith("}")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``key=value`` env file into a dict (blank/``#`` lines ignored; no quote stripping,
    no ``${VAR}`` expansion). Empty dict when the file is absent."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def detect_db_credentials(services: dict[str, Any], db_service: Optional[str], project: str) -> dict[str, str]:
    """Detect ``POSTGRES_USER`` / ``POSTGRES_DB`` from the source DB service environment.

    Password is never read from source — it is always a fresh UAT synthetic. Defaults:
    user ``postgres``, db ``<project>_uat``.
    """
    user, db_name = "postgres", f"{project}_uat"
    if db_service and isinstance(services.get(db_service), dict):
        env = services[db_service].get("environment") or {}
        if isinstance(env, dict):
            if env.get("POSTGRES_USER") and not _is_var_expansion(env["POSTGRES_USER"]):
                user = str(env["POSTGRES_USER"])
            if env.get("POSTGRES_DB") and not _is_var_expansion(env["POSTGRES_DB"]):
                db_name = str(env["POSTGRES_DB"])
    return {"POSTGRES_USER": user, "POSTGRES_DB": db_name}


def _rewrite_db_connection_var(key: str, value: Any, *, user: str, db_name: str, password: str, db_host: str) -> str:
    """Rewrite a DB connection env var to the UAT db service host + detected creds + shared password."""
    if key in {"DB_HOST", "POSTGRES_HOST"}:
        return db_host
    if key in {"DB_PORT", "POSTGRES_PORT"}:
        return UAT_DB_PORT
    if key in {"DB_NAME", "POSTGRES_DB"}:
        return db_name
    if key in {"DB_USER", "POSTGRES_USER"}:
        return user
    if key in {"DB_PASSWORD", "POSTGRES_PASSWORD"}:
        return password
    if key == "DATABASE_URL":
        return f"postgresql://{user}:{password}@{db_host}:{UAT_DB_PORT}/{db_name}"
    return str(value)


def generate_uat_env(
    *,
    slug: str,
    project: str,
    version: str,
    services: dict[str, Any],
    be_service: Optional[str],
    db_service: Optional[str],
    source_env_example: dict[str, str],
    db_user: str,
    db_name: str,
    shared_db_password: str,
    preserved_secrets: Optional[dict[str, str]] = None,
) -> str:
    """Render the UAT ``.env`` content (detected DB creds + synthetic backend secrets).

    Baseline = the source ``.env.example`` (lists every var the backend reads, incl. secrets set
    only at runtime); the BE service ``environment`` union-merges on top (compose has the
    authoritative DB host/port overrides). ``${VAR}`` → ``__UAT_SYNTHETIC__``; DB-connection vars
    → UAT db host + shared password; ``*_PASSWORD/_SECRET/_KEY/_TOKEN`` → synthetic (or the
    ``preserved_secrets`` value on a redeploy). The top-level POSTGRES_* + DB_PASSWORD lines are
    always written so the postgres service (which reads ``${POSTGRES_PASSWORD}``) and the backend
    agree on the same synthetic password.
    """
    preserved_secrets = preserved_secrets or {}
    db_host = db_service or "postgres"

    lines = [
        f"# UAT environment for slug={slug} (generated by backend/services/uat_provisioner.py)",
        "# UAT credentials are SYNTHETIC — separate from production (per CLAUDE.md §4).",
        "",
        f"POSTGRES_USER={db_user}",
        f"POSTGRES_PASSWORD={shared_db_password}",
        f"POSTGRES_DB={db_name}",
        f"DB_PASSWORD={shared_db_password}",
        f"UAT_SLUG={slug}",
        f"PROJECT_VERSION={version}",
    ]

    # Backend env: .env.example baseline, then BE service environment overlay.
    raw_env: dict[str, Any] = dict(source_env_example)
    if be_service and isinstance(services.get(be_service), dict):
        be_env = services[be_service].get("environment") or {}
        if isinstance(be_env, dict):
            raw_env.update(be_env)

    rendered: dict[str, str] = {}
    for key, value in raw_env.items():
        key_str = str(key)
        if key_str in {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DB_PASSWORD"}:
            continue  # already emitted above (single source of the shared password)
        if _is_var_expansion(value):
            rendered[key_str] = USER_SECRET_PLACEHOLDER
        elif key_str in DB_CONNECTION_VARS:
            rendered[key_str] = _rewrite_db_connection_var(
                key_str, value, user=db_user, db_name=db_name, password=shared_db_password, db_host=db_host
            )
        elif key_str.lower().endswith(SECRET_SUFFIXES):
            rendered[key_str] = preserved_secrets.get(key_str) or _synthetic_secret(key_str)
        else:
            rendered[key_str] = str(value)

    if rendered:
        lines.append("")
        lines.append("# Detected + synthetic backend env vars")
        for key in sorted(rendered):
            lines.append(f"{key}={rendered[key]}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Redeploy preservation (never silently rotate a data-bearing instance)
# ---------------------------------------------------------------------------


def load_existing_env_secrets(uat_dir: Path) -> dict[str, str]:
    """Secret-bearing key→value pairs from an existing UAT ``.env`` (for redeploy preservation):
    keys ending with :data:`SECRET_SUFFIXES` plus ``DB_PASSWORD`` / ``POSTGRES_PASSWORD``."""
    out: dict[str, str] = {}
    for key, value in _parse_env_file(uat_dir / ".env").items():
        if key.lower().endswith(SECRET_SUFFIXES) or key in {"DB_PASSWORD", "POSTGRES_PASSWORD"}:
            out[key] = value
    return out


def parse_compose_extra_hosts(uat_dir: Path, be_service: str = "backend") -> list[str]:
    """The BE service ``extra_hosts`` from an existing UAT compose (preserve host-gateway/hairpin
    entries a live instance grew). Handles both list (``- "h:ip"``) and mapping (``h: ip``) forms."""
    compose_path = uat_dir / "docker-compose.yml"
    if not compose_path.is_file():
        return []
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    services = data.get("services") or {}
    svc = services.get(be_service) or services.get("backend") or {}
    raw = svc.get("extra_hosts") or []
    if isinstance(raw, dict):
        return [f"{k}:{v}" for k, v in raw.items()]
    if isinstance(raw, list):
        return [str(h) for h in raw]
    return []


# ---------------------------------------------------------------------------
# Traefik labels (exact pattern copied from the Phase-1 ledger migration)
# ---------------------------------------------------------------------------


def frontend_traefik_labels(slug: str, fe_internal_port: int) -> list[str]:
    """The FE service's Traefik labels — the catch-all ``Host(uat-<slug>.isnex.eu)`` route."""
    host = f"uat-{slug}.{UAT_DOMAIN_SUFFIX}"
    return [
        "traefik.enable=true",
        f"traefik.docker.network={PROXY_NETWORK}",
        f"traefik.http.routers.uat-{slug}.rule=Host(`{host}`)",
        f"traefik.http.routers.uat-{slug}.entrypoints=web",
        f"traefik.http.routers.uat-{slug}.priority={FE_ROUTER_PRIORITY}",
        f"traefik.http.services.uat-{slug}.loadbalancer.server.port={fe_internal_port}",
    ]


def backend_traefik_labels(slug: str, be_internal_port: int) -> list[str]:
    """The BE service's Traefik labels — the higher-priority ``/api`` PathPrefix split."""
    host = f"uat-{slug}.{UAT_DOMAIN_SUFFIX}"
    return [
        "traefik.enable=true",
        f"traefik.docker.network={PROXY_NETWORK}",
        f"traefik.http.routers.uat-{slug}-api.rule=Host(`{host}`) && PathPrefix(`/api`)",
        f"traefik.http.routers.uat-{slug}-api.entrypoints=web",
        f"traefik.http.routers.uat-{slug}-api.priority={BE_ROUTER_PRIORITY}",
        f"traefik.http.services.uat-{slug}-api.loadbalancer.server.port={be_internal_port}",
    ]


# ---------------------------------------------------------------------------
# Compose transform (CR-1)
# ---------------------------------------------------------------------------


def _abs_build_context(context: str, project_path: Path) -> str:
    """Resolve a source build ``context`` to an absolute path under the source project."""
    if os.path.isabs(context):
        return context
    relative = context.lstrip("./") if context.startswith(".") else context
    return os.path.normpath(str(project_path / relative))


def _merge_labels(existing: Any, traefik: list[str]) -> list[str]:
    """Append Traefik labels to a service's existing labels (list or mapping form)."""
    out: list[str] = []
    if isinstance(existing, dict):
        out.extend(f"{k}={v}" for k, v in existing.items())
    elif isinstance(existing, list):
        out.extend(str(x) for x in existing)
    out.extend(traefik)
    return out


def _add_proxy_network(svc: dict[str, Any]) -> None:
    """Attach the external ``nex-proxy-net`` to a service (list or mapping ``networks`` form)."""
    nets = svc.get("networks")
    if isinstance(nets, dict):
        nets.setdefault(PROXY_NETWORK, None)
    elif isinstance(nets, list):
        if PROXY_NETWORK not in nets:
            nets.append(PROXY_NETWORK)
    else:
        svc["networks"] = [PROXY_NETWORK]


def build_uat_compose(
    *,
    slug: str,
    project: str,
    project_path: Path,
    source: dict[str, Any],
    roles: dict[str, Optional[str]],
    db_user: str,
    db_name: str,
    loopback_base_port: Optional[int] = None,
    extra_backend_hosts: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build the final UAT compose **dict** from the parsed source compose (CR-1).

    Per-service transform: rename ``container_name`` + built-image tag to ``uat-<slug>-<svc>``,
    absolutize build contexts, ``restart: "no"``, preserve environment/volumes/healthcheck/
    depends_on/extra_hosts/networks, and force the DB password to the synthetic
    ``${POSTGRES_PASSWORD}``. The FE + BE services additionally join ``nex-proxy-net`` and get the
    exact Traefik labels. Top-level ``name: uat-<slug>`` namespaces all unnamed networks/volumes;
    ``nex-proxy-net`` is added as an external network. Host ports are dropped for routing — only if
    ``loopback_base_port`` is given are FE/BE/DB bound to loopback debug ports (FE base, BE base+100,
    DB base+200).
    """
    fe_name, be_name, db_name_svc = roles["frontend"], roles["backend"], roles["db"]
    src_services: dict[str, Any] = source["services"]

    services: dict[str, Any] = {}
    for name, src_svc in src_services.items():
        svc = copy.deepcopy(src_svc) if isinstance(src_svc, dict) else {}

        svc["container_name"] = f"uat-{slug}-{name}"
        svc["restart"] = "no"

        # Built services → tag + absolutize context; image-only services pass through.
        build = svc.get("build")
        if build is not None:
            svc["image"] = f"uat-{slug}-{name}:latest"
            if isinstance(build, str):
                svc["build"] = {"context": _abs_build_context(build, project_path)}
            elif isinstance(build, dict):
                ctx = build.get("context", ".")
                build["context"] = _abs_build_context(str(ctx), project_path)

        # Drop source host-port bindings (Traefik routes by network, not host ports).
        svc.pop("ports", None)

        # DB password is always a fresh UAT synthetic, sourced from .env.
        if name == db_name_svc:
            env = svc.get("environment")
            if isinstance(env, dict):
                env["POSTGRES_PASSWORD"] = "${POSTGRES_PASSWORD}"
            elif env is None:
                svc["environment"] = {"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"}

        # Carry over extra backend hosts grown by a live instance (redeploy preservation).
        if name == be_name and extra_backend_hosts:
            existing = svc.get("extra_hosts")
            merged = (
                list(existing)
                if isinstance(existing, list)
                else ([f"{k}:{v}" for k, v in existing.items()] if isinstance(existing, dict) else [])
            )
            for host in extra_backend_hosts:
                if host not in merged:
                    merged.append(host)
            svc["extra_hosts"] = merged

        services[name] = svc

    # Traefik routing on FE + BE (join nex-proxy-net + exact labels).
    if fe_name and fe_name in services:
        fe_port = detect_internal_port(src_services[fe_name], 80)
        _add_proxy_network(services[fe_name])
        services[fe_name]["labels"] = _merge_labels(
            services[fe_name].get("labels"), frontend_traefik_labels(slug, fe_port)
        )
    if be_name and be_name in services:
        be_port = detect_internal_port(src_services[be_name], 8000)
        _add_proxy_network(services[be_name])
        services[be_name]["labels"] = _merge_labels(
            services[be_name].get("labels"), backend_traefik_labels(slug, be_port)
        )

    # Optional loopback debug ports (FE base / BE base+100 / DB base+200).
    if loopback_base_port is not None:
        if fe_name and fe_name in services:
            fe_port = detect_internal_port(src_services[fe_name], 80)
            services[fe_name]["ports"] = [f"127.0.0.1:{loopback_base_port}:{fe_port}"]
        if be_name and be_name in services:
            be_port = detect_internal_port(src_services[be_name], 8000)
            services[be_name]["ports"] = [f"127.0.0.1:{loopback_base_port + 100}:{be_port}"]
        if db_name_svc and db_name_svc in services:
            db_port = detect_internal_port(src_services[db_name_svc], 5432)
            services[db_name_svc]["ports"] = [f"127.0.0.1:{loopback_base_port + 200}:{db_port}"]

    # Networks: keep source nets (project name namespaces unnamed ones), drop explicit names on
    # internal nets so they cannot collide with the source project, preserve external nets, add proxy.
    networks: dict[str, Any] = {}
    for net_name, net_def in (source.get("networks") or {}).items():
        net = copy.deepcopy(net_def) if isinstance(net_def, dict) else {}
        if not net.get("external"):
            net.pop("name", None)
        networks[net_name] = net or None
    networks[PROXY_NETWORK] = {"external": True}

    # Volumes: keep source volume keys (project name namespaces unnamed ones); strip explicit names.
    volumes: dict[str, Any] = {}
    for vol_name, vol_def in (source.get("volumes") or {}).items():
        vol = copy.deepcopy(vol_def) if isinstance(vol_def, dict) else {}
        vol.pop("name", None)
        volumes[vol_name] = vol or None

    compose: dict[str, Any] = {"name": f"uat-{slug}", "services": services, "networks": networks}
    if volumes:
        compose["volumes"] = volumes
    return compose


# ---------------------------------------------------------------------------
# Rendering (template-backed, CR-1/CR-2)
# ---------------------------------------------------------------------------


def _yaml_block(data: Any) -> str:
    """Block-style YAML dump preserving insertion order (no key sorting) for compose output."""
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=4096)


def render_uat_compose(compose: dict[str, Any]) -> str:
    """Render the UAT compose dict to YAML text via ``templates/uat/docker-compose.yml.j2``.

    The template owns the header + ``name:`` line; the dynamic networks/services/volumes are
    emitted via a registered ``to_yaml`` filter (robust block-style dump of an arbitrary
    N-service structure — far safer than hand-written Jinja over nested compose).
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["to_yaml"] = _yaml_block
    template = env.get_template("uat/docker-compose.yml.j2")
    body = {k: v for k, v in compose.items() if k != "name"}
    return template.render(COMPOSE_NAME=compose["name"], COMPOSE_BODY=body)


# ---------------------------------------------------------------------------
# provision_uat (CR-2) — the importable entrypoint
# ---------------------------------------------------------------------------


@dataclass
class ProvisionResult:
    """Outcome of :func:`provision_uat` — paths + the resolved layout the caller (CLI / engine) needs."""

    uat_slug: str
    uat_dir: Path
    compose_path: Path
    env_path: Path
    services: list[str]
    fe_service: Optional[str]
    be_service: Optional[str]
    db_service: Optional[str]
    be_internal_port: Optional[int]
    fe_internal_port: Optional[int]
    be_health_path: str
    loopback_base_port: Optional[int]
    is_redeploy: bool
    warnings: list[str] = field(default_factory=list)


def provision_uat(
    project_slug: str,
    uat_slug: str,
    *,
    version: str = "v0.0.0-dev",
    projects_root: Path = PROJECTS_ROOT,
    uat_root: Path = UAT_ROOT,
    loopback_base_port: Optional[int] = None,
    rotate_secrets: bool = False,
) -> ProvisionResult:
    """Render ``/opt/uat/<uat_slug>/{docker-compose.yml,.env}`` + create dirs for ``project_slug``.

    Pure provisioning — it does **NOT** build or start anything (Phase-3 ``_run_uat_deploy`` does).
    Synchronous; an async caller should wrap it via ``asyncio.to_thread``.

    Redeploy safety: when a ``.env`` already exists and ``rotate_secrets`` is False, existing
    secrets + the BE ``extra_hosts`` are PRESERVED (never silently rotate a data-bearing instance);
    ``rotate_secrets=True`` forces a fresh re-provision. The ``.env`` is ``chmod 600`` and its
    contents are never logged/returned.
    """
    validate_uat_slug(uat_slug)
    validate_uat_slug(project_slug)

    project_path = projects_root / project_slug
    if not project_path.is_dir():
        raise FileNotFoundError(f"project directory not found: {project_path}")

    source = load_source_compose(project_path)
    src_services: dict[str, Any] = source["services"]
    roles = identify_service_roles(src_services)

    uat_dir = uat_root / uat_slug

    # Redeploy preservation (per the live-instance contract).
    is_redeploy = (uat_dir / ".env").is_file() and not rotate_secrets
    preserved_secrets = load_existing_env_secrets(uat_dir) if is_redeploy else {}
    extra_backend_hosts = parse_compose_extra_hosts(uat_dir, roles["backend"] or "backend") if is_redeploy else []

    db_creds = detect_db_credentials(src_services, roles["db"], project_slug)
    db_user, db_name = db_creds["POSTGRES_USER"], db_creds["POSTGRES_DB"]

    shared_db_password = (
        preserved_secrets.get("POSTGRES_PASSWORD") or preserved_secrets.get("DB_PASSWORD") or secrets.token_hex(32)
    )

    compose = build_uat_compose(
        slug=uat_slug,
        project=project_slug,
        project_path=project_path,
        source=source,
        roles=roles,
        db_user=db_user,
        db_name=db_name,
        loopback_base_port=loopback_base_port,
        extra_backend_hosts=extra_backend_hosts,
    )

    env_example = _parse_env_file(project_path / ".env.example")
    env_content = generate_uat_env(
        slug=uat_slug,
        project=project_slug,
        version=version,
        services=src_services,
        be_service=roles["backend"],
        db_service=roles["db"],
        source_env_example=env_example,
        db_user=db_user,
        db_name=db_name,
        shared_db_password=shared_db_password,
        preserved_secrets=preserved_secrets,
    )

    # Write: dirs first, then compose + chmod-600 .env.
    uat_dir.mkdir(parents=True, exist_ok=True)
    (uat_dir / "snapshots").mkdir(exist_ok=True)
    (uat_dir / "logs").mkdir(exist_ok=True)

    compose_path = uat_dir / "docker-compose.yml"
    compose_path.write_text(render_uat_compose(compose), encoding="utf-8")

    env_path = uat_dir / ".env"
    env_path.write_text(env_content, encoding="utf-8")
    env_path.chmod(0o600)

    warnings: list[str] = []
    if roles["frontend"] is None:
        warnings.append(
            f"no frontend service detected in {project_slug}'s compose — Traefik has no default "
            f"route for uat-{uat_slug}.{UAT_DOMAIN_SUFFIX} (only /api would resolve)"
        )
    if roles["backend"] is None:
        warnings.append(f"no backend service detected in {project_slug}'s compose — no /api Traefik route")

    return ProvisionResult(
        uat_slug=uat_slug,
        uat_dir=uat_dir,
        compose_path=compose_path,
        env_path=env_path,
        services=list(src_services.keys()),
        fe_service=roles["frontend"],
        be_service=roles["backend"],
        db_service=roles["db"],
        be_internal_port=detect_internal_port(src_services[roles["backend"]], 8000) if roles["backend"] else None,
        fe_internal_port=detect_internal_port(src_services[roles["frontend"]], 80) if roles["frontend"] else None,
        be_health_path=resolve_be_health_path(
            src_services.get(roles["backend"]) if roles["backend"] else None, project_path
        ),
        loopback_base_port=loopback_base_port,
        is_redeploy=is_redeploy,
        warnings=warnings,
    )
