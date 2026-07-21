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
    ``host.docker.internal:host-gateway``) and any *internal* networks they declare. Routing is
    via **Traefik** (Phase-1 infra), NOT host ports — the FE + BE services join the external
    ``nex-proxy-net`` and carry the exact Traefik labels copied from the live ledger migration.
    The ONLY external network a UAT may join is ``nex-proxy-net``; any other external network
    (e.g. a project's ``nex-network: external: true``) is a hard provisioning error (§5.3).

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
``uat-<slug>-<service>``; the only external network allowed is ``nex-proxy-net`` (any other
external network is rejected as a hard provisioning error).
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import secrets
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jinja2
import yaml

# Repo root = .../nex-studio (this file is backend/services/uat_provisioner.py).
NEX_STUDIO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = NEX_STUDIO_ROOT / "templates"

UAT_ROOT = Path("/opt/uat")
# PROD instances live under the per-customer control-plane root (design §2): a PROD deploy renders
# ``/opt/customers/<customer-slug>/<full-project-slug>/`` instead of ``/opt/uat/<instance-slug>/``.
PROD_ROOT = Path("/opt/customers")
PROJECTS_ROOT = Path("/opt/projects")

# Port-allocation state file (shared with scripts/_uat_lib.allocate_port — same repo-root path) so a
# teardown can reclaim the slug's port. ``TEARDOWN_TIMEOUT`` bounds the ``docker compose down`` shellout.
PORT_STATE_FILE = NEX_STUDIO_ROOT / ".uat-ports.json"
TEARDOWN_TIMEOUT = 180

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

# The ONE env var that is a HUMAN's login (the initial admin password), not a machine secret. The manager must
# KNOW it to use the deployed app, so a per-customer deploy sets it to the customer secret (which the manager
# themselves set), never a random synthetic they could never discover (self-sufficiency kernel, 2026-07-11).
# Convention for NEX-Studio-generated apps (the bootstrap reads this key). All OTHER *_password/etc. stay random.
ADMIN_LOGIN_ENV_KEY = "ADMIN_INITIAL_PASSWORD"
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

# H1 (CR-1): driver↔URL self-validation. SQLAlchemy **sync** postgres DBAPIs a ``postgresql+<driver>://``
# URL can name — asyncpg is deliberately EXCLUDED (it is async, reached via ``postgresql+asyncpg://`` or — as
# in nex-ledger — raw ``asyncpg`` with a bare URL and NO SQLAlchemy create_engine lookup). A bare
# ``postgresql://`` makes SQLAlchemy default to psycopg2, so a project that ships ONLY pg8000/psycopg crashes
# at migrate (ModuleNotFoundError) — the nex-manager dogfood bug.
SQLALCHEMY_PG_DRIVERS = {"psycopg2", "psycopg", "pg8000"}

# pyproject dependency name → SQLAlchemy driver token (both extras-stripped and extras-bearing forms).
_PG_DEP_TO_DRIVER = {
    "psycopg2": "psycopg2",
    "psycopg2-binary": "psycopg2",
    "psycopg": "psycopg",
    "psycopg[binary]": "psycopg",
    "pg8000": "pg8000",
}

# Multi-var detection: ``DATABASE_URL`` plus any ``*_DATABASE_URL`` (e.g. ``READ_DATABASE_URL``).
DB_URL_SUFFIX = "_database_url"

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


def has_alembic_migrate_service(services: dict[str, Any]) -> bool:
    """True when a compose service runs ``alembic upgrade`` as its command.

    Such a dedicated ``migrate`` service already migrates the DB on ``docker compose up``
    (as a ``depends_on`` gate), so the deploy must NOT run migrations a second time. Apps
    without one (e.g. nex-shopify) get a post-``up`` ``alembic upgrade head`` instead
    (:func:`orchestrator._run_post_up_migration`). Detection is by command, not service
    name, so it holds whatever the migrate service is called.
    """
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        command = svc.get("command")
        text = " ".join(str(c) for c in command) if isinstance(command, list) else str(command or "")
        if "alembic" in text and "upgrade" in text:
            return True
    return False


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


def _var_expansion_default(value: str) -> Optional[str]:
    """v4.0.18: the DEFAULT of a ``${VAR:-default}`` / ``${VAR-default}`` expansion (the text after the ``-``),
    or ``None`` for a bare ``${VAR}`` / a required ``${VAR:?err}`` with no usable default. Used so the UAT
    ``.env`` renders the COMPOSE default for a non-secret var (e.g. ``GENESIS_SOURCE:-mock``) instead of the
    ``__UAT_SYNTHETIC__`` placeholder — a placeholder crashed apps whose config VALIDATES the value
    (nex-shopify: ``genesis_source`` ∈ {mock, http} rejected ``__UAT_SYNTHETIC__``)."""
    inner = value[2:-1]  # strip the leading ``${`` and trailing ``}``
    for marker in (":-", "-"):  # ``:-`` (unset OR empty) and ``-`` (unset) both supply a fallback
        idx = inner.find(marker)
        if idx != -1:
            return inner[idx + len(marker) :]
    return None  # ``${VAR}`` / ``${VAR:?err}`` — no default to honour


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


def read_paired_manager_launch(manager_env_path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read the paired NEX Manager Deploy's ``LAUNCH_SIGNING_KEY`` + ``DEPLOY_SLUG``.

    A NEX-Manager token-launch module must verify launch tokens with the SAME key the
    Manager signs them with. The Manager Deploy lives as a sibling under the same
    customer root (``<root>/<customer>/nex-manager/.env``), so a token-launch app's
    ``MANAGER_LAUNCH_SIGNING_KEY`` is wired from there — not synthesised (a random key
    would never match the Manager → every launch 401s). Returns ``(None, None)`` when
    no Manager is deployed there (the app then falls back to preserved/empty — i.e.
    token-launch stays off until a Manager is paired). The key is a secret — never logged.
    """
    mgr = _parse_env_file(manager_env_path)  # {} when the file is absent
    return (mgr.get("LAUNCH_SIGNING_KEY") or None, mgr.get("DEPLOY_SLUG") or None)


def detect_sqlalchemy_pg_drivers(project_path: Path) -> Optional[set[str]]:
    """The SQLAlchemy sync postgres driver tokens the source project DECLARES as dependencies (H1, CR-1).

    Reads ``<project>/backend/pyproject.toml`` first, else ``<project>/pyproject.toml`` (nex-asistent /
    nex-studio keep it at root). Collects dependency names from ALL of: ``[tool.poetry.dependencies]`` AND
    every ``[tool.poetry.group.*.dependencies]`` (dict keys — Poetry 1.2+ groups); ``[project].dependencies``
    AND every list in ``[project.optional-dependencies]`` (PEP-621 extras); AND every list in
    ``[dependency-groups]`` (PEP-735). PEP-621/735 requirement strings are reduced to the bare name (up to the
    first of ``[<>=!~;`` or a space). All names are lowercased + extras-stripped, then mapped via
    :data:`_PG_DEP_TO_DRIVER`.

    Scope widened per Director 2026-06-23: the original main-table-only scope would downgrade a real bare-URL
    + pg8000 bug to a WARN whenever the driver is declared in a group/extra — a hole in the very guard.

    Returns the resolved set (possibly EMPTY — e.g. an asyncpg-only project with no SQLAlchemy pg driver) on
    success; returns ``None`` when NO pyproject was found / it failed to parse (→ the caller WARNs rather than
    fails). NEVER reads ``.env``/secret files; NEVER raises.
    """
    for candidate in (project_path / "backend" / "pyproject.toml", project_path / "pyproject.toml"):
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return None
        names: list[str] = []

        # Poetry dependency tables — names are dict KEYS (main table + every Poetry 1.2+ group).
        poetry = (data.get("tool") or {}).get("poetry") or {}
        poetry_tables: list[Any] = [poetry.get("dependencies")]
        poetry_tables.extend(
            grp.get("dependencies") for grp in (poetry.get("group") or {}).values() if isinstance(grp, dict)
        )
        for table in poetry_tables:
            if isinstance(table, dict):
                names.extend(str(k) for k in table)

        # PEP-621 / PEP-735 requirement-string LISTS: main deps, every extra, every dependency-group.
        project = data.get("project") or {}
        req_lists: list[Any] = [project.get("dependencies")]
        req_lists.extend((project.get("optional-dependencies") or {}).values())
        req_lists.extend((data.get("dependency-groups") or {}).values())
        for req_list in req_lists:
            if isinstance(req_list, list):
                # PEP-735 entries may be include-group tables (dicts) rather than strings — skip those.
                names.extend(re.split(r"[\[<>=!~; ]", d)[0] for d in req_list if isinstance(d, str))

        drivers: set[str] = set()
        for name in names:
            norm = name.strip().lower()
            token = _PG_DEP_TO_DRIVER.get(norm) or _PG_DEP_TO_DRIVER.get(norm.split("[", 1)[0])
            if token:
                drivers.add(token)
        return drivers
    return None


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
        # Preserve the source SQLAlchemy dialect (e.g. ``postgresql+pg8000``) — hardcoding bare
        # ``postgresql://`` makes SQLAlchemy default to psycopg2, which a pg8000-only project (no
        # psycopg2 dep) can't import → the UAT migrate service dies "No module named 'psycopg2'".
        scheme = str(value).split("://", 1)[0] if "://" in str(value) else "postgresql"
        return f"{scheme}://{user}:{password}@{db_host}:{UAT_DB_PORT}/{db_name}"
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
    admin_password: Optional[str] = None,
    manager_env_path: Optional[Path] = None,
) -> str:
    """Render the UAT ``.env`` content (detected DB creds + synthetic backend secrets).

    Baseline = the source ``.env.example`` (lists every var the backend reads, incl. secrets set
    only at runtime); the BE service ``environment`` union-merges on top (compose has the
    authoritative DB host/port overrides). ``${VAR}`` → ``__UAT_SYNTHETIC__``; DB-connection vars
    → UAT db host + shared password; ``*_PASSWORD/_SECRET/_KEY/_TOKEN`` → synthetic (or the
    ``preserved_secrets`` value on a redeploy). The top-level POSTGRES_* + DB_PASSWORD lines are
    always written so the postgres service (which reads ``${POSTGRES_PASSWORD}``) and the backend
    agree on the same synthetic password.

    Token-launch modules (those declaring ``MANAGER_LAUNCH_SIGNING_KEY``) are the ONE exception
    to synthesis: that key + ``MANAGER_DEPLOY_SLUG`` are wired from the paired NEX Manager Deploy
    (``manager_env_path``) so the module verifies launch tokens with the same key the Manager
    signs them with. Everything else (incl. the module-private ``MANAGER_SESSION_SIGNING_KEY``)
    stays synthetic/preserved as usual.
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

    # NEX-Manager token-launch (self-describing): an app that declares
    # ``MANAGER_LAUNCH_SIGNING_KEY`` is launched from the paired Manager and must share
    # that Manager's launch key (a synthetic one never matches → every launch 401s). Wire
    # it + the Manager's DEPLOY_SLUG from the paired Manager Deploy's ``.env``; resolved
    # lazily so non-token apps never touch it.
    mgr_launch_key: Optional[str] = None
    mgr_deploy_slug: Optional[str] = None
    if manager_env_path is not None and "MANAGER_LAUNCH_SIGNING_KEY" in raw_env:
        mgr_launch_key, mgr_deploy_slug = read_paired_manager_launch(manager_env_path)

    rendered: dict[str, str] = {}
    for key, value in raw_env.items():
        key_str = str(key)
        if key_str in {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DB_PASSWORD"}:
            continue  # already emitted above (single source of the shared password)
        if key_str == "MANAGER_LAUNCH_SIGNING_KEY":
            # Paired Manager's key wins (propagates a Manager key rotation on redeploy);
            # else preserve the prior value; else empty (token-launch off, launch route
            # cleanly rejects) — never a synthetic that would 401 every real launch.
            rendered[key_str] = mgr_launch_key or preserved_secrets.get(key_str) or ""
            continue
        if key_str == "MANAGER_DEPLOY_SLUG":
            rendered[key_str] = mgr_deploy_slug or ""
            continue
        if _is_var_expansion(value):
            # v4.0.18: honour a ``${VAR:-default}`` default on a NON-secret var — it IS the app's intended value
            # (e.g. GENESIS_SOURCE:-mock / SHOPIFY_SOURCE:-fake). Blindly writing __UAT_SYNTHETIC__ crashed apps
            # that VALIDATE the value (nex-shopify UAT 2026-07-20: genesis_source ∈ {mock,http} rejected it, the
            # backend exited 1). Secrets + a bare ${VAR}/${VAR:?err} (no default) stay synthetic — a value the
            # manager could never discover otherwise, so a placeholder they replace is correct.
            default = _var_expansion_default(value)
            if default is not None and not key_str.lower().endswith(SECRET_SUFFIXES):
                rendered[key_str] = default
            else:
                rendered[key_str] = USER_SECRET_PLACEHOLDER
        elif key_str in DB_CONNECTION_VARS:
            rendered[key_str] = _rewrite_db_connection_var(
                key_str, value, user=db_user, db_name=db_name, password=shared_db_password, db_host=db_host
            )
        elif key_str == ADMIN_LOGIN_ENV_KEY and admin_password:
            # The manager's initial admin login — set it to the customer secret they KNOW (not a random
            # synthetic they could never discover). Wins over both the synthetic path and preserved_secrets so a
            # redeploy re-aligns it. Never logged (§4).
            rendered[key_str] = admin_password
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


def validate_rendered_db_drivers(
    env_content: str, declared_drivers: Optional[set[str]], *, project_slug: str
) -> tuple[list[str], list[str]]:
    """Assert the rendered UAT ``.env`` carries an importable postgres ``DATABASE_URL`` (H1, CR-1).

    Returns a TYPED ``(fail_msgs, warn_msgs)`` — NOT a string-``"FAIL:"``-prefix sentinel — so the caller
    drives control flow on the list, never on string parsing. Inspects every ``DATABASE_URL`` /
    ``*_DATABASE_URL`` value; non-postgres schemes (sqlite/mysql/…) and empty / ``__UAT_SYNTHETIC__`` values
    are out of scope and skipped. The ONLY hard FAIL is the unambiguous bug signature — a bare
    ``postgresql://`` while the project ships a sync SQLAlchemy driver that is NOT psycopg2 (create_engine
    would default to the absent psycopg2 → ModuleNotFoundError at migrate). Everything ambiguous WARNs.
    """
    fail_msgs: list[str] = []
    warn_msgs: list[str] = []
    for raw in env_content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key_lower = key.strip().lower()
        if not (key_lower == "database_url" or key_lower.endswith(DB_URL_SUFFIX)):
            continue
        value = value.strip()
        if not value or value == USER_SECRET_PLACEHOLDER:
            continue
        scheme = value.split("://", 1)[0]
        backend = scheme.split("+", 1)[0].lower()
        if backend != "postgresql":
            continue  # sqlite / mysql / etc. — out of scope
        if "+" in scheme:
            # Explicit +driver — OK. Optional signal: a sync driver named but not declared as a dependency.
            named = scheme.split("+", 1)[1].lower()
            if declared_drivers and named in SQLALCHEMY_PG_DRIVERS and named not in declared_drivers:
                warn_msgs.append(
                    f"DATABASE_URL for '{project_slug}' names +{named} but it is not a declared dependency "
                    f"({_fmt_drivers(declared_drivers)}) — verify the source pyproject"
                )
            continue
        # Bare ``postgresql://`` (no explicit driver) — SQLAlchemy defaults to psycopg2.
        if declared_drivers is None:
            warn_msgs.append(
                f"could not verify DB driver for '{project_slug}' (no parsable pyproject); "
                f"bare postgresql:// defaults to psycopg2"
            )
        elif "psycopg2" in declared_drivers:
            continue  # legitimate — psycopg2 is the SQLAlchemy default for a bare URL
        elif declared_drivers:
            fail_msgs.append(
                f"bare 'postgresql://' DATABASE_URL but project ships SQLAlchemy driver(s) "
                f"{_fmt_drivers(declared_drivers)} and NOT psycopg2 — create_engine would default to the "
                f"absent psycopg2 (ModuleNotFoundError at migrate). SOURCE FIX REQUIRED (not transient): the "
                f"source DATABASE_URL must declare the +driver, e.g. postgresql+pg8000://."
            )
        else:
            warn_msgs.append(
                f"could not confirm a SQLAlchemy pg driver for '{project_slug}' (none declared — e.g. "
                f"asyncpg-only); bare postgresql:// assumed intentional"
            )
    return fail_msgs, warn_msgs


def _fmt_drivers(drivers: set[str]) -> str:
    """Deterministic ``{a, b}`` rendering of a driver set (sorted — set repr order is non-deterministic)."""
    return "{" + ", ".join(sorted(drivers)) + "}"


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


def _instance_naming(
    environment: str, uat_slug: str, customer_slug: Optional[str], app: Optional[str]
) -> tuple[str, str]:
    """The ``(name_base, host)`` for an instance's compose/container/image/router ids + public vhost.

    Per-customer (customer_slug + app given): PROD → ``<customer>-<app>``, UAT → ``uat-<customer>-<app>``
    (audit fix 2026-07-11 — the per-customer UAT is per-PROJECT, not the old flat ``uat-<customer>-uat``).
    Project-level UAT (no customer_slug, the uat-deploy.py path) → ``uat-<uat_slug>`` (unchanged). The host is
    always ``<name_base>.<UAT_DOMAIN_SUFFIX>``, so both route via Traefik on the clean host.
    """
    if customer_slug and app:
        base = f"{customer_slug}-{app}"
        name_base = base if environment == "prod" else f"uat-{base}"
    elif environment == "prod":
        raise ValueError("prod instance naming requires customer_slug + app")
    else:
        name_base = f"uat-{uat_slug}"
    return name_base, f"{name_base}.{UAT_DOMAIN_SUFFIX}"


def frontend_traefik_labels(name_base: str, fe_internal_port: int, host: str) -> list[str]:
    """The FE service's Traefik labels — the catch-all ``Host(<host>)`` route.

    ``name_base`` is the router/service id base (``uat-<slug>`` for UAT, ``<customer>-<app>`` for
    PROD); ``host`` the matching public vhost. Traefik stays ENABLED for both environments — PROD
    keeps its labels, just with the clean host + prefix-free router ids.
    """
    return [
        "traefik.enable=true",
        f"traefik.docker.network={PROXY_NETWORK}",
        f"traefik.http.routers.{name_base}.rule=Host(`{host}`)",
        f"traefik.http.routers.{name_base}.entrypoints=web",
        f"traefik.http.routers.{name_base}.priority={FE_ROUTER_PRIORITY}",
        f"traefik.http.services.{name_base}.loadbalancer.server.port={fe_internal_port}",
    ]


def backend_traefik_labels(name_base: str, be_internal_port: int, host: str) -> list[str]:
    """The BE service's Traefik labels — the higher-priority ``/api`` PathPrefix split.

    ``name_base``/``host`` are per-env (see :func:`frontend_traefik_labels`); the router/service id
    is ``<name_base>-api``.
    """
    return [
        "traefik.enable=true",
        f"traefik.docker.network={PROXY_NETWORK}",
        f"traefik.http.routers.{name_base}-api.rule=Host(`{host}`) && PathPrefix(`/api`)",
        f"traefik.http.routers.{name_base}-api.entrypoints=web",
        f"traefik.http.routers.{name_base}-api.priority={BE_ROUTER_PRIORITY}",
        f"traefik.http.services.{name_base}-api.loadbalancer.server.port={be_internal_port}",
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
    """Attach the external ``nex-proxy-net`` to a service (list or mapping ``networks`` form).

    Networking fix (spec §3): a service with NO explicit ``networks`` is implicitly on the compose
    ``default`` network (where ``db``/``migrate`` live). Giving it an explicit list of ONLY
    ``nex-proxy-net`` would silently drop it OFF ``default`` — so the backend could no longer reach
    ``db`` (the manual-deploy bug). When there is no explicit list we therefore attach BOTH
    ``default`` and ``nex-proxy-net``. A service that already declares a shared internal network keeps
    it (it can already reach ``db``) and only gains ``nex-proxy-net``. Applies to both prod and uat.
    """
    nets = svc.get("networks")
    if isinstance(nets, dict):
        nets.setdefault(PROXY_NETWORK, None)
    elif isinstance(nets, list):
        if PROXY_NETWORK not in nets:
            nets.append(PROXY_NETWORK)
    else:
        svc["networks"] = ["default", PROXY_NETWORK]


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
    environment: str = "uat",
    customer_slug: Optional[str] = None,
    app: Optional[str] = None,
) -> dict[str, Any]:
    """Build the final compose **dict** from the parsed source compose (CR-1), environment-aware.

    Per-service transform: rename ``container_name`` + built-image tag to ``<name_base>-<svc>``
    (``name_base`` = ``uat-<slug>`` for UAT / ``<customer>-<app>`` for PROD, design §2), absolutize
    build contexts, set ``restart`` (uat ``"no"`` / prod ``"unless-stopped"``), preserve
    environment/volumes/healthcheck/depends_on/extra_hosts/networks, and force the DB password to the
    synthetic ``${POSTGRES_PASSWORD}``. The FE + BE services additionally join ``nex-proxy-net`` and
    get the exact Traefik labels on the per-env host. Top-level ``name: <name_base>`` namespaces all
    unnamed networks/volumes; ``nex-proxy-net`` is added as the (only) external network. A source
    external network other than ``nex-proxy-net`` raises ``ValueError`` (§5.3). Host ports are dropped
    for routing — only if ``loopback_base_port`` is given are FE/BE/DB bound to loopback debug ports
    (FE base, BE base+100, DB base+200).
    """
    fe_name, be_name, db_name_svc = roles["frontend"], roles["backend"], roles["db"]
    src_services: dict[str, Any] = source["services"]

    name_base, host = _instance_naming(environment, slug, customer_slug, app)
    restart_policy = "unless-stopped" if environment == "prod" else "no"

    services: dict[str, Any] = {}
    for name, src_svc in src_services.items():
        svc = copy.deepcopy(src_svc) if isinstance(src_svc, dict) else {}

        svc["container_name"] = f"{name_base}-{name}"
        # One-shot services (e.g. migrate ``alembic upgrade head``) are marked ``restart: "no"``
        # in the source and are depended on via ``service_completed_successfully``. Forcing PROD's
        # ``unless-stopped`` on them makes docker restart them after exit 0 → the dependency never
        # satisfies → deploy hangs ("timeout waiting for dependencies"). Preserve the one-shot
        # marker; apply the env restart policy only to long-running services.
        _src_restart = svc.get("restart")
        svc["restart"] = "no" if (_src_restart == "no" or _src_restart is False) else restart_policy

        # Built services → tag + absolutize context; image-only services pass through.
        build = svc.get("build")
        if build is not None:
            svc["image"] = f"{name_base}-{name}:latest"
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
            # NB: the loop var must NOT be named ``host`` — that would clobber the outer ``host`` (the public
            # vhost from _instance_naming) with the last extra_hosts entry (e.g. "host.docker.internal:host-
            # gateway"), which then poisons the Traefik Host() rules below → the instance becomes publicly
            # unreachable. Only bites on REDEPLOY (extra_backend_hosts non-empty). Regression: andros-payables
            # PROD outage 2026-07-10.
            for extra_host in extra_backend_hosts:
                if extra_host not in merged:
                    merged.append(extra_host)
            svc["extra_hosts"] = merged

        services[name] = svc

    # Traefik routing on FE + BE (join nex-proxy-net + exact labels).
    if fe_name and fe_name in services:
        fe_port = detect_internal_port(src_services[fe_name], 80)
        _add_proxy_network(services[fe_name])
        services[fe_name]["labels"] = _merge_labels(
            services[fe_name].get("labels"), frontend_traefik_labels(name_base, fe_port, host)
        )
    if be_name and be_name in services:
        be_port = detect_internal_port(src_services[be_name], 8000)
        _add_proxy_network(services[be_name])
        services[be_name]["labels"] = _merge_labels(
            services[be_name].get("labels"), backend_traefik_labels(name_base, be_port, host)
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
    # internal nets so they cannot collide with the source project, add proxy. The ONLY external
    # network a UAT may join is ``nex-proxy-net`` (the Traefik bus); any OTHER external network
    # (e.g. nex-asistent's ``nex-network: external: true``) is a hard provisioning error — it would
    # attach the UAT to a PROD network, risking DNS/container collisions + a cross-environment leak
    # (icc-deploy §5.3, ALL archetypes). Catch the misconfig here rather than silently stripping it.
    networks: dict[str, Any] = {}
    for net_name, net_def in (source.get("networks") or {}).items():
        net = copy.deepcopy(net_def) if isinstance(net_def, dict) else {}
        if net.get("external"):
            external_name = net.get("name") or net_name  # the real docker network name
            if external_name != PROXY_NETWORK:
                raise ValueError(
                    f"external network {external_name!r} is not allowed in a UAT compose "
                    f"(only {PROXY_NETWORK!r} may be external) — declare it as an internal "
                    f"network or remove it before provisioning"
                )
            continue  # the canonical nex-proxy-net is added below; never duplicate the source's
        net.pop("name", None)
        networks[net_name] = net or None
    networks[PROXY_NETWORK] = {"external": True}

    # Volumes: keep source volume keys (project name namespaces unnamed ones); strip explicit names.
    volumes: dict[str, Any] = {}
    for vol_name, vol_def in (source.get("volumes") or {}).items():
        vol = copy.deepcopy(vol_def) if isinstance(vol_def, dict) else {}
        vol.pop("name", None)
        volumes[vol_name] = vol or None

    compose: dict[str, Any] = {"name": name_base, "services": services, "networks": networks}
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
    environment: str = "uat",
    customer_slug: Optional[str] = None,
    app: Optional[str] = None,
    full_project_slug: Optional[str] = None,
    prod_root: Path = PROD_ROOT,
    admin_password: Optional[str] = None,
) -> ProvisionResult:
    """Render an instance's ``{docker-compose.yml,.env}`` + create dirs for ``project_slug``.

    Environment-aware (design §2): UAT renders under ``/opt/uat/<uat_slug>/`` with ``uat-<slug>-*``
    names; PROD renders under ``/opt/customers/<customer_slug>/<full_project_slug>/`` with
    ``<customer_slug>-<app>-*`` names, the clean ``<customer_slug>-<app>.isnex.eu`` Traefik host, and
    ``restart: unless-stopped``. ``environment`` defaults to ``'uat'`` (back-compat); PROD requires
    ``customer_slug`` + ``app`` + ``full_project_slug``. BOTH route via Traefik on ``nex-proxy-net``.

    Pure provisioning — it does **NOT** build or start anything (Phase-3 ``_run_uat_deploy`` /
    ``_run_prod_deploy`` does). Synchronous; an async caller should wrap it via ``asyncio.to_thread``.

    Redeploy safety: when a ``.env`` already exists and ``rotate_secrets`` is False, existing
    secrets + the BE ``extra_hosts`` are PRESERVED (never silently rotate a data-bearing instance);
    ``rotate_secrets=True`` forces a fresh re-provision. The ``.env`` is ``chmod 600`` and its
    contents are never logged/returned.
    """
    if environment not in ("uat", "prod"):
        raise ValueError(f"unknown environment {environment!r} (expected 'uat' or 'prod')")
    validate_uat_slug(uat_slug)
    validate_uat_slug(project_slug)
    # Per-customer instances (cockpit deploy, BOTH uat + prod) nest under ``<root>/<customer>/<project>`` and
    # are named ``[uat-]<customer>-<app>``; the components must all be present + traversal-safe. PROD is always
    # per-customer; a per-customer UAT is signalled by ANY of the three being set (audit fix 2026-07-11). The
    # project-level uat-deploy.py path passes NONE → the flat ``<uat_root>/<uat_slug>`` layout below (unchanged).
    per_customer = bool(customer_slug or app or full_project_slug) or environment == "prod"
    if per_customer:
        if not (customer_slug and app and full_project_slug):
            raise ValueError("per-customer provisioning requires customer_slug, app, and full_project_slug together")
        validate_uat_slug(customer_slug)
        validate_uat_slug(app)
        validate_uat_slug(full_project_slug)

    project_path = projects_root / project_slug
    if not project_path.is_dir():
        raise FileNotFoundError(f"project directory not found: {project_path}")

    source = load_source_compose(project_path)
    src_services: dict[str, Any] = source["services"]
    roles = identify_service_roles(src_services)

    # Per-customer → nested ``<root>/<customer>/<project>`` (PROD /opt/customers, UAT /opt/uat); project-level
    # UAT → flat ``/opt/uat/<slug>`` (unchanged).
    if per_customer:
        uat_dir = (prod_root if environment == "prod" else uat_root) / customer_slug / full_project_slug
    else:
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
        environment=environment,
        customer_slug=customer_slug,
        app=app,
    )

    # Paired NEX Manager Deploy — a sibling under the same customer root
    # (``<root>/<customer>/nex-manager/.env``). Only per-customer instances (cockpit
    # deploy) can be Manager-launched; the flat project-level UAT path has no customer,
    # so no pairing (token-launch stays off there).
    manager_env_path: Optional[Path] = None
    if per_customer and customer_slug:
        manager_env_path = (prod_root if environment == "prod" else uat_root) / customer_slug / "nex-manager" / ".env"

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
        admin_password=admin_password,
        manager_env_path=manager_env_path,
    )

    # H1 (CR-1): driver↔URL self-validation BEFORE any file is written — fail LOUD at provision time for
    # the unambiguous bug signature (bare postgresql:// while the project ships a sync driver ≠ psycopg2),
    # leaving NOTHING on disk; WARN for every ambiguous case. The source DATABASE_URL must be fixed, not the
    # render — so the message says SOURCE FIX REQUIRED.
    warnings: list[str] = []
    declared_drivers = detect_sqlalchemy_pg_drivers(project_path)
    fail_msgs, warn_msgs = validate_rendered_db_drivers(env_content, declared_drivers, project_slug=project_slug)
    if fail_msgs:
        raise ValueError("; ".join(fail_msgs))
    warnings.extend(warn_msgs)

    # Write: dirs first, then compose + chmod-600 .env.
    uat_dir.mkdir(parents=True, exist_ok=True)
    (uat_dir / "snapshots").mkdir(exist_ok=True)
    (uat_dir / "logs").mkdir(exist_ok=True)

    compose_path = uat_dir / "docker-compose.yml"
    compose_path.write_text(render_uat_compose(compose), encoding="utf-8")

    env_path = uat_dir / ".env"
    env_path.write_text(env_content, encoding="utf-8")
    env_path.chmod(0o600)

    _name_base, host = _instance_naming(environment, uat_slug, customer_slug, app)
    if roles["frontend"] is None:
        warnings.append(
            f"no frontend service detected in {project_slug}'s compose — Traefik has no default "
            f"route for {host} (only /api would resolve)"
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


# ---------------------------------------------------------------------------
# Teardown (v0.9.0 Phase 3, CR-2) — orphan prevention on project delete
# ---------------------------------------------------------------------------


def reclaim_port(uat_slug: str, *, port_state_file: Path = PORT_STATE_FILE) -> bool:
    """Remove ``uat_slug`` from the ``.uat-ports.json`` allocation. Returns True if a port was reclaimed.

    NEVER raises (a malformed/absent state file just yields ``False``) — port reclamation must never
    block a project delete.
    """
    try:
        if not port_state_file.is_file():
            return False
        state = json.loads(port_state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or uat_slug not in state:
            return False
        del state[uat_slug]
        port_state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except (OSError, ValueError):
        return False


def teardown_uat(
    uat_slug: str,
    *,
    uat_root: Path = UAT_ROOT,
    port_state_file: Path = PORT_STATE_FILE,
    timeout: int = TEARDOWN_TIMEOUT,
) -> tuple[bool, str]:
    """Tear down an orphaned UAT when its project is deleted (CR-2). Returns ``(ok, detail)``; NEVER raises.

    ``docker compose -f /opt/uat/<uat_slug>/docker-compose.yml down -v`` (sync subprocess — mirrors
    :func:`backend.services.orchestrator._run_uat_deploy`'s shellout, sync because the delete route is
    sync) + reclaim the allocated ``.uat-ports.json`` port. Traefik auto-de-routes once the containers are
    gone (no host/nginx change). A missing compose is a no-op success (nothing to tear down) — the port is
    still reclaimed. **Version supersede is NOT a teardown**; this runs only on project delete.
    """
    try:
        validate_uat_slug(uat_slug)
    except ValueError as exc:
        return False, f"invalid uat_slug: {exc}"

    compose_path = uat_root / uat_slug / "docker-compose.yml"
    if not compose_path.is_file():
        reclaim_port(uat_slug, port_state_file=port_state_file)
        return True, "no compose — nothing to tear down"

    try:
        proc = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "down", "-v"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"teardown failed to run: {exc}"

    reclaim_port(uat_slug, port_state_file=port_state_file)
    if proc.returncode == 0:
        return True, "OK"
    tail = (proc.stdout or "").strip()[-300:]
    return False, (f"exit {proc.returncode}: {tail}" if tail else f"exit {proc.returncode}")
