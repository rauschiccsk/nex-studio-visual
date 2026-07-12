"""Prod-mode tests for the environment-aware provisioner + deploy runner.

Mirrors the UAT provisioner tests (``tests/test_uat_provisioner.py``) for the PROD branch of
``docs/specs/provisioner-environment-aware.md`` (Approach B — PROD routes via Traefik exactly like
UAT). Asserts a ``environment='prod'`` render produces the clean per-customer layout:
``<customer>-<app>-*`` names, compose name ``<customer>-<app>``, Traefik host
``<customer>-<app>.isnex.eu`` with router ids WITHOUT the ``uat-`` prefix, root
``/opt/customers/<customer>/<full-project-slug>``, ``restart: unless-stopped``, and the backend on
BOTH the compose ``default`` net (db reachability, §3) AND ``nex-proxy-net``. Also re-asserts that
``environment='uat'`` (default) still yields ``uat-`` names — byte-identical to today.

Co-located with the UAT provisioner tests (both in the ``tests`` testpath) so the prod + uat twins
are discoverable together; the shared module is exercised by the FULL suite (CR-061 lesson).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

# A minimal FE+BE+DB stack with NO explicit ``networks`` — every service is implicitly on the compose
# ``default`` network, which is exactly the shape that exposes the §3 bug (a service given ONLY
# nex-proxy-net drops off ``default`` and can no longer reach ``db``). The networking fix keeps the
# backend on both.
PROD_COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: payables
          POSTGRES_DB: payables
          POSTGRES_PASSWORD: ${DB_PASSWORD}
      migrate:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["alembic", "upgrade", "head"]
        restart: "no"
        depends_on:
          db:
            condition: service_healthy
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        ports: ["8000:8000"]
        env_file: [.env]
        depends_on:
          db:
            condition: service_healthy
      frontend:
        build:
          context: ./frontend
        ports: ["3000:80"]
        depends_on:
          backend:
            condition: service_healthy
    """
)


def _make_project(tmp_path: Path, slug: str, compose: str) -> Path:
    project_path = tmp_path / "projects" / slug
    project_path.mkdir(parents=True)
    (project_path / "docker-compose.yml").write_text(compose)
    return project_path


def _provision_prod(tmp_path, **kw):
    """Provision a PROD instance for nex-payables / customer andros (default kwargs)."""
    _make_project(tmp_path, kw.get("project_slug", "nex-payables"), kw.pop("compose", PROD_COMPOSE))
    return P.provision_uat(
        kw.pop("project_slug", "nex-payables"),
        kw.pop("uat_slug", "andros-prod"),
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug=kw.pop("customer_slug", "andros"),
        app=kw.pop("app", "payables"),
        full_project_slug=kw.pop("full_project_slug", "nex-payables"),
        **kw,
    )


# ---------- PROD naming + layout (spec §6) ----------


def test_prod_render_root_is_opt_customers(tmp_path):
    """PROD renders under ``/opt/customers/<customer>/<full-project-slug>/`` (§2), NOT ``/opt/uat``."""
    res = _provision_prod(tmp_path)
    expected_dir = tmp_path / "customers" / "andros" / "nex-payables"
    assert res.uat_dir == expected_dir
    assert res.compose_path == expected_dir / "docker-compose.yml"
    assert res.compose_path.is_file()
    assert res.env_path == expected_dir / ".env"
    assert oct(res.env_path.stat().st_mode)[-3:] == "600"  # chmod 600 preserved for prod too


def test_prod_compose_name_and_container_image_names(tmp_path):
    """Compose name ``<customer>-<app>`` + container/image names ``<customer>-<app>-<svc>`` (no ``uat-``)."""
    res = _provision_prod(tmp_path)
    data = yaml.safe_load(res.compose_path.read_text())

    assert data["name"] == "andros-payables"
    assert data["services"]["backend"]["container_name"] == "andros-payables-backend"
    assert data["services"]["frontend"]["container_name"] == "andros-payables-frontend"
    assert data["services"]["db"]["container_name"] == "andros-payables-db"
    assert data["services"]["backend"]["image"] == "andros-payables-backend:latest"
    assert data["services"]["frontend"]["image"] == "andros-payables-frontend:latest"
    # No ``uat-`` prefix anywhere in the names.
    for svc in data["services"].values():
        assert not svc["container_name"].startswith("uat-")
    assert not data["name"].startswith("uat-")


def test_prod_restart_policy_long_lived_vs_oneshot(tmp_path):
    """PROD: long-lived services are ``unless-stopped``, but one-shot services (``migrate`` —
    ``restart: "no"`` in the source, depended on via ``service_completed_successfully``) STAY ``no``.
    Forcing ``unless-stopped`` on a completed one-shot restart-loops it → the dependency never
    satisfies → deploy hangs. Regression guard for the live-validation bug (2026-07-07)."""
    res = _provision_prod(tmp_path)
    data = yaml.safe_load(res.compose_path.read_text())
    assert data["services"]["migrate"]["restart"] == "no", "one-shot migrate must NOT auto-restart in prod"
    for name in ("db", "backend", "frontend"):
        assert data["services"][name]["restart"] == "unless-stopped", f"{name} must be unless-stopped in prod"


def test_prod_traefik_host_and_router_ids_without_uat_prefix(tmp_path):
    """FE catch-all + BE ``/api`` split route the clean ``<customer>-<app>.isnex.eu`` host; router/service
    ids are ``<customer>-<app>`` / ``-api`` — WITHOUT the ``uat-`` prefix. Traefik stays enabled."""
    res = _provision_prod(tmp_path)
    data = yaml.safe_load(res.compose_path.read_text())

    assert data["services"]["frontend"]["labels"] == [
        "traefik.enable=true",
        "traefik.docker.network=nex-proxy-net",
        "traefik.http.routers.andros-payables.rule=Host(`andros-payables.isnex.eu`)",
        "traefik.http.routers.andros-payables.entrypoints=web",
        "traefik.http.routers.andros-payables.priority=10",
        "traefik.http.services.andros-payables.loadbalancer.server.port=80",
    ]
    assert data["services"]["backend"]["labels"] == [
        "traefik.enable=true",
        "traefik.docker.network=nex-proxy-net",
        "traefik.http.routers.andros-payables-api.rule=Host(`andros-payables.isnex.eu`) && PathPrefix(`/api`)",
        "traefik.http.routers.andros-payables-api.entrypoints=web",
        "traefik.http.routers.andros-payables-api.priority=20",
        "traefik.http.services.andros-payables-api.loadbalancer.server.port=8000",
    ]
    # No router id carries the uat- prefix.
    all_labels = data["services"]["frontend"]["labels"] + data["services"]["backend"]["labels"]
    assert not any("routers.uat-" in lbl or "services.uat-" in lbl for lbl in all_labels)


def test_prod_backend_on_both_default_and_proxy_network(tmp_path):
    """§3 networking fix: the backend joins BOTH the compose ``default`` net (so it can reach ``db``,
    which stays on ``default``) AND ``nex-proxy-net`` (Traefik). Applies in prod too."""
    res = _provision_prod(tmp_path)
    data = yaml.safe_load(res.compose_path.read_text())

    be_nets = data["services"]["backend"]["networks"]
    assert "default" in be_nets and "nex-proxy-net" in be_nets
    assert data["networks"]["nex-proxy-net"] == {"external": True}
    # db carries no explicit networks → it stays on the implicit compose ``default`` net (reachable).
    assert "networks" not in data["services"]["db"]


def test_prod_still_routes_by_traefik_not_host_ports(tmp_path):
    """PROD also drops source host-port bindings — Traefik routes by network (Approach B)."""
    res = _provision_prod(tmp_path)
    data = yaml.safe_load(res.compose_path.read_text())
    for name, svc in data["services"].items():
        assert "ports" not in svc, f"{name} must not bind host ports in prod (Traefik routing)"


def test_prod_requires_customer_app_and_full_slug(tmp_path):
    """``environment='prod'`` without customer_slug/app/full_project_slug is a hard error (fail-before-write)."""
    _make_project(tmp_path, "nex-payables", PROD_COMPOSE)
    with pytest.raises(ValueError, match="per-customer provisioning requires"):
        P.provision_uat(
            "nex-payables",
            "andros-prod",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
            prod_root=tmp_path / "customers",
            environment="prod",
        )


def test_unknown_environment_rejected(tmp_path):
    _make_project(tmp_path, "nex-payables", PROD_COMPOSE)
    with pytest.raises(ValueError, match="unknown environment"):
        P.provision_uat(
            "nex-payables",
            "andros-staging",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
            environment="staging",
        )


# ---------- UAT unchanged (default environment) ----------


def test_uat_default_still_yields_uat_names_and_host(tmp_path):
    """The default ``environment='uat'`` render is byte-identical to today: ``uat-<slug>-*`` names,
    compose name ``uat-<slug>``, host ``uat-<slug>.isnex.eu`` — and lands under ``/opt/uat``, not
    ``/opt/customers``."""
    _make_project(tmp_path, "nex-payables", PROD_COMPOSE)
    res = P.provision_uat(
        "nex-payables",
        "andros-uat",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
    )
    data = yaml.safe_load(res.compose_path.read_text())

    assert res.uat_dir == tmp_path / "uat" / "andros-uat"
    assert data["name"] == "uat-andros-uat"
    assert data["services"]["backend"]["container_name"] == "uat-andros-uat-backend"
    assert data["services"]["backend"]["restart"] == "no"
    assert (
        "traefik.http.routers.uat-andros-uat.rule=Host(`uat-andros-uat.isnex.eu`)"
        in (data["services"]["frontend"]["labels"])
    )
    # The networking fix applies to uat too (no-explicit-networks source → backend on default + proxy).
    assert data["services"]["backend"]["networks"] == ["default", "nex-proxy-net"]


# ---------- deploy runner: PROD layout derived at the 4-kwarg seam ----------


def test_default_deploy_runner_prod_derives_layout(monkeypatch):
    """The built-in runner derives the PROD layout from ``(project_slug, uat_slug)`` (``-prod`` suffix) —
    threading environment='prod' + customer/app/full-slug to the provisioner + calling _run_prod_deploy —
    while its public seam stays the 4-kwarg ``(project_slug, uat_slug, version_number, force_fresh)``."""
    import asyncio

    from backend.services import deploy as deploy_service
    from backend.services import orchestrator, uat_provisioner

    captured = {}

    class _Result:
        warnings: list[str] = []
        fe_service = "frontend"

    def _fake_provision(project_slug, uat_slug, *, version, rotate_secrets, **kw):
        captured["provision"] = {"project_slug": project_slug, "uat_slug": uat_slug, **kw}
        return _Result()

    async def _fake_run_prod(project_slug, customer_slug, app, full_project_slug, version_number=None):
        captured["prod_deploy"] = (project_slug, customer_slug, app, full_project_slug)
        return True, "OK"

    monkeypatch.setattr(uat_provisioner, "provision_uat", _fake_provision)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _fake_run_prod)

    ok, detail, url = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-payables", uat_slug="andros-prod", version_number="v1.0.0", force_fresh=False
        )
    )

    assert ok is True
    assert captured["provision"]["environment"] == "prod"
    assert captured["provision"]["customer_slug"] == "andros"
    assert captured["provision"]["app"] == "payables"
    assert captured["provision"]["full_project_slug"] == "nex-payables"
    assert captured["prod_deploy"] == ("nex-payables", "andros", "payables", "nex-payables")
    assert url == "https://andros-payables.isnex.eu"


def test_default_deploy_runner_uat_passes_no_prod_kwargs(monkeypatch):
    """A ``-uat`` slug takes the UAT branch. Since the 2026-07-11 audit fix the UAT branch ALSO derives the
    per-project layout (``environment='uat'`` + customer/app/full-slug threaded to the provisioner + the plain
    ``_run_uat_deploy`` redeploy) — so the runner takes ``_run_uat_deploy`` (never ``_run_prod_deploy``) and the
    URL is the per-project ``uat-<customer>-<app>`` host."""
    import asyncio

    from backend.services import deploy as deploy_service
    from backend.services import orchestrator, uat_provisioner

    captured = {}

    class _Result:
        warnings: list[str] = []
        fe_service = "frontend"

    def _fake_provision(project_slug, uat_slug, *, version, rotate_secrets, **kw):
        captured["uat_slug"] = uat_slug
        captured["provision"] = kw
        return _Result()

    async def _fake_run_uat(project_slug, uat_slug, **kw):
        captured["uat_deploy"] = (project_slug, uat_slug)
        return True, "OK"

    monkeypatch.setattr(uat_provisioner, "provision_uat", _fake_provision)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_run_uat)

    ok, detail, url = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-payables", uat_slug="andros-uat", version_number="v0.1.0", force_fresh=False
        )
    )

    assert ok is True
    assert captured["provision"]["environment"] == "uat"  # the UAT branch, never prod
    assert captured["uat_deploy"] == ("nex-payables", "andros-uat")
    assert url == "https://uat-andros-payables.isnex.eu"


# ---------- orchestrator: PROD deploy compose path + serve-verify container names ----------


class _FakeProc:
    def __init__(self, returncode, output):
        self.returncode = returncode
        self._output = output

    async def communicate(self):
        return self._output, b""

    def kill(self):
        pass


def test_uat_compose_path_prod_branch():
    """``_uat_compose_path`` prod branch → ``/opt/customers/<customer>/<full-slug>/docker-compose.yml``;
    the uat branch is untouched."""
    from backend.services import orchestrator

    prod = orchestrator._uat_compose_path(
        "andros-payables", environment="prod", customer_slug="andros", full_project_slug="nex-payables"
    )
    assert prod == Path("/opt/customers/andros/nex-payables/docker-compose.yml")
    assert orchestrator._uat_compose_path("ledger") == Path("/opt/uat/ledger/docker-compose.yml")


async def test_run_prod_deploy_uses_customer_root_compose(monkeypatch):
    """``_run_prod_deploy`` redeploys the PROD compose under ``/opt/customers/...`` (never ``/opt/uat``)."""
    from backend.services import orchestrator

    captured = {}

    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        return _FakeProc(0, b"deploy log tail")

    async def _serves_ok(*_a, **_k):
        return True, "OK"

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(orchestrator, "_verify_uat_serves", _serves_ok)

    ok, detail = await orchestrator._run_prod_deploy("nex-payables", "andros", "payables", "nex-payables")

    assert ok is True and detail == "OK"
    joined = " ".join(captured["cmd"])
    assert "/opt/customers/andros/nex-payables/docker-compose.yml" in joined
    assert "/opt/uat/" not in joined


async def test_verify_uat_serves_prod_probes_customer_app_container(monkeypatch, tmp_path):
    """PROD serve-verify cross-probes the frontend by its ``<customer>-<app>-<svc>`` container name (not
    the ``uat-`` name), reading service ports from the source compose."""
    from backend.services import orchestrator

    prod_root = tmp_path / "customers"
    projects_root = tmp_path / "projects"
    prod_dir = prod_root / "andros" / "nex-payables"
    prod_dir.mkdir(parents=True)
    (prod_dir / "docker-compose.yml").write_text("services: {}\n")
    src = projects_root / "nex-payables"
    src.mkdir(parents=True)
    (src / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n    build: .\n    ports:\n      - '8000:8000'\n"
        "  frontend:\n    build: ./frontend\n    ports:\n      - '3000:80'\n"
        "  db:\n    image: postgres:16-alpine\n"
    )
    monkeypatch.setattr(orchestrator, "PROD_ROOT", prod_root)
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", projects_root)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)

    probes: list[list[str]] = []

    async def _probe(cmd, timeout):
        probes.append(cmd)
        return (0, "status 404") if ("python" in cmd and "localhost" in " ".join(cmd)) else (0, "status 200")

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _probe)

    ok, detail = await orchestrator._verify_uat_serves(
        "nex-payables",
        "andros-payables",
        environment="prod",
        customer_slug="andros",
        app="payables",
        full_project_slug="nex-payables",
    )

    assert (ok, detail) == (True, "OK")
    assert any("andros-payables-frontend:80/" in " ".join(c) for c in probes), "FE probed by prod container name"
    # No ``uat-`` prefixed INSTANCE container name in prod probes. (The public-route probe legitimately hits the
    # shared ``nex-uat-traefik`` ingress host — infra, not an instance container — so match the instance prefix.)
    assert not any("uat-andros-payables" in " ".join(c) for c in probes), "no uat- prefixed container in prod probes"
