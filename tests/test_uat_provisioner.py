"""Tests for backend/services/uat_provisioner.py (v0.9.0 Phase 2, CR-1 + CR-2 + CR-3).

Derived from the spec's Self-verify (docs/specs/versions/v0.9.0/spec/phase-2-provisioner.md §Self-verify):
given nex-asistent's source compose (qdrant + BE + FE + external nex-network + Ollama extra_hosts),
the provisioner renders a UAT compose that (a) includes ALL services incl. qdrant, (b) preserves
extra_hosts + the external network, (c) adds nex-proxy-net + the exact Traefik labels to FE+BE with
the right internal ports, (d) routes by Traefik not host ports. Plus derive_uat_slug cases and a
3-service render.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

# A fixture shaped like nex-asistent's source compose, AUGMENTED with the frontend service the
# spec's self-verify describes (the live nex-asistent compose omits its buildable FE — see the DONE
# report gap note). qdrant + external nex-network + Ollama extra_hosts are the real nex-asistent shape.
ASISTENT_COMPOSE = textwrap.dedent(
    """
    services:
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        container_name: nex-asistent-backend
        ports: ["10180:8000"]
        env_file: [.env]
        environment:
          DB_HOST: postgres
          QDRANT_URL: "http://qdrant:6333"
          OLLAMA_URL: "http://host.docker.internal:9132"
        extra_hosts:
          - "host.docker.internal:host-gateway"
        volumes:
          - nex-asistent-uploads:/data/uploads
        depends_on:
          postgres:
            condition: service_healthy
          qdrant:
            condition: service_healthy
        networks: [nex-asistent-net, nex-network]
      frontend:
        build:
          context: ./frontend
          dockerfile: Dockerfile
        container_name: nex-asistent-frontend
        ports: ["10181:80"]
        depends_on:
          backend:
            condition: service_healthy
        networks: [nex-asistent-net]
      postgres:
        image: postgres:16-alpine
        container_name: nex-asistent-postgres
        environment:
          POSTGRES_DB: nex_asistent
          POSTGRES_USER: nex_asistent
          POSTGRES_PASSWORD: ${DB_PASSWORD}
        volumes:
          - nex-asistent-pg-data:/var/lib/postgresql/data
        networks: [nex-asistent-net]
      qdrant:
        image: qdrant/qdrant:v1.13.6
        container_name: nex-asistent-qdrant
        ports: ["10183:6333"]
        volumes:
          - nex-asistent-qdrant-data:/qdrant/storage
        networks: [nex-asistent-net]
    volumes:
      nex-asistent-pg-data:
      nex-asistent-qdrant-data:
      nex-asistent-uploads:
    networks:
      nex-asistent-net:
        driver: bridge
      nex-network:
        external: true
    """
)

THREE_SERVICE_COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: appuser
          POSTGRES_DB: appdb
          POSTGRES_PASSWORD: ${DB_PASSWORD}
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


def _make_project(tmp_path: Path, slug: str, compose: str, *, env_example: str | None = None) -> Path:
    project_path = tmp_path / "projects" / slug
    project_path.mkdir(parents=True)
    (project_path / "docker-compose.yml").write_text(compose)
    if env_example is not None:
        (project_path / ".env.example").write_text(env_example)
    return project_path


def _provision(tmp_path, project_slug, uat_slug, compose, **kw):
    _make_project(tmp_path, project_slug, compose, env_example=kw.pop("env_example", None))
    return P.provision_uat(
        project_slug,
        uat_slug,
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        **kw,
    )


# ---------- CR-3: derive_uat_slug ----------


@pytest.mark.parametrize(
    "slug, expected",
    [
        ("nex-ledger", "ledger"),
        ("nex-inbox", "inbox"),
        ("nex-asistent", "asistent"),
        ("demo", "demo"),  # no prefix → unchanged
        ("nex-nex-thing", "nex-thing"),  # only ONE leading nex- stripped
    ],
)
def test_derive_uat_slug_cases(slug, expected):
    assert P.derive_uat_slug(slug) == expected


def test_derive_uat_slug_accepts_project_object():
    class _P:
        slug = "nex-ledger"

    assert P.derive_uat_slug(_P()) == "ledger"


def test_derive_uat_slug_rejects_invalid_result():
    with pytest.raises(ValueError):
        P.derive_uat_slug("BAD/slug")


# ---------- CR-1: generalized N-service passthrough (nex-asistent shape) ----------


def test_provision_includes_all_services_incl_qdrant(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    assert set(data["services"]) == {"backend", "frontend", "postgres", "qdrant"}
    assert res.services == ["backend", "frontend", "postgres", "qdrant"]


def test_provision_preserves_extra_hosts_and_external_network(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    # Ollama host-gateway survives.
    assert "host.docker.internal:host-gateway" in data["services"]["backend"]["extra_hosts"]
    # External nex-network preserved verbatim.
    assert data["networks"]["nex-network"] == {"external": True}
    # Backend stays attached to the external net.
    assert "nex-network" in data["services"]["backend"]["networks"]


def test_provision_adds_proxy_network_and_exact_traefik_labels(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())

    assert data["networks"]["nex-proxy-net"] == {"external": True}
    assert "nex-proxy-net" in data["services"]["frontend"]["networks"]
    assert "nex-proxy-net" in data["services"]["backend"]["networks"]

    # Exact FE labels (catch-all Host route @ priority 10, FE internal port 80).
    assert data["services"]["frontend"]["labels"] == [
        "traefik.enable=true",
        "traefik.docker.network=nex-proxy-net",
        "traefik.http.routers.uat-asistent.rule=Host(`uat-asistent.isnex.eu`)",
        "traefik.http.routers.uat-asistent.entrypoints=web",
        "traefik.http.routers.uat-asistent.priority=10",
        "traefik.http.services.uat-asistent.loadbalancer.server.port=80",
    ]
    # Exact BE labels (/api PathPrefix @ priority 20, BE internal port 8000 — container side of 10180:8000).
    assert data["services"]["backend"]["labels"] == [
        "traefik.enable=true",
        "traefik.docker.network=nex-proxy-net",
        "traefik.http.routers.uat-asistent-api.rule=Host(`uat-asistent.isnex.eu`) && PathPrefix(`/api`)",
        "traefik.http.routers.uat-asistent-api.entrypoints=web",
        "traefik.http.routers.uat-asistent-api.priority=20",
        "traefik.http.services.uat-asistent-api.loadbalancer.server.port=8000",
    ]


def test_provision_routes_by_traefik_not_host_ports(tmp_path):
    """By default (no loopback base) NO service binds a host port — Traefik routes by network."""
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    for name, svc in data["services"].items():
        assert "ports" not in svc, f"{name} must not bind host ports (Traefik routing)"


def test_provision_renames_containers_and_built_images(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    assert data["name"] == "uat-asistent"
    assert data["services"]["backend"]["container_name"] == "uat-asistent-backend"
    assert data["services"]["backend"]["image"] == "uat-asistent-backend:latest"
    assert data["services"]["qdrant"]["container_name"] == "uat-asistent-qdrant"
    # Pull-through image preserved (qdrant not rebuilt).
    assert data["services"]["qdrant"]["image"] == "qdrant/qdrant:v1.13.6"
    # All services ephemeral.
    for svc in data["services"].values():
        assert svc["restart"] == "no"


def test_provision_absolutizes_build_context(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    project_path = str(tmp_path / "projects" / "nex-asistent")
    assert data["services"]["backend"]["build"]["context"] == project_path
    assert data["services"]["frontend"]["build"]["context"] == str(Path(project_path) / "frontend")


def test_provision_forces_synthetic_db_password(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())
    # DB password is sourced from .env (never the source ${DB_PASSWORD} passthrough).
    assert data["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD}"


def test_provision_result_reports_roles_and_ports(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    assert res.fe_service == "frontend"
    assert res.be_service == "backend"
    assert res.db_service == "postgres"
    assert res.be_internal_port == 8000
    assert res.fe_internal_port == 80
    assert res.warnings == []


# ---------- .env synthesis (§4) ----------


def test_env_has_synthetic_db_creds_and_chmod_600(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    env = res.env_path.read_text()
    assert "POSTGRES_USER=nex_asistent" in env
    assert "POSTGRES_DB=nex_asistent" in env
    # POSTGRES_PASSWORD + DB_PASSWORD share one synthetic value (no ${...} left).
    pw_lines = {ln.split("=", 1)[1] for ln in env.splitlines() if ln.startswith(("POSTGRES_PASSWORD=", "DB_PASSWORD="))}
    assert len(pw_lines) == 1 and "${" not in next(iter(pw_lines))
    assert len(next(iter(pw_lines))) >= 32
    assert oct(res.env_path.stat().st_mode)[-3:] == "600"


def test_env_secrets_synthetic_and_var_placeholder(tmp_path):
    env_example = "JWT_SECRET_KEY=\nLLM_API_TOKEN=${LLM_API_TOKEN}\nPLAIN_SETTING=keepme\n"
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE, env_example=env_example)
    env = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in res.env_path.read_text().splitlines() if "=" in ln}
    # *_KEY → base64 (decodes to 32 bytes); ${VAR} → placeholder; plain value preserved.
    import base64

    assert len(base64.b64decode(env["JWT_SECRET_KEY"], validate=True)) == 32
    assert env["LLM_API_TOKEN"] == "__UAT_SYNTHETIC__"
    assert env["PLAIN_SETTING"] == "keepme"


def test_env_never_contains_var_expansion_for_secrets(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    assert "${" not in res.env_path.read_text()


# ---------- Redeploy preservation ----------


def test_redeploy_preserves_secrets_and_extra_hosts(tmp_path):
    # First provision establishes the instance.
    res1 = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    env1 = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in res1.env_path.read_text().splitlines() if "=" in ln}

    # Simulate a live instance that grew an extra host (IMAP hairpin) on the backend.
    compose1 = yaml.safe_load(res1.compose_path.read_text())
    compose1["services"]["backend"].setdefault("extra_hosts", []).append("mail.isnex.eu:192.168.55.250")
    res1.compose_path.write_text(yaml.safe_dump(compose1, sort_keys=False))

    # Redeploy (default — preserve).
    res2 = P.provision_uat("nex-asistent", "asistent", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")
    assert res2.is_redeploy is True
    env2 = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in res2.env_path.read_text().splitlines() if "=" in ln}
    assert env2["POSTGRES_PASSWORD"] == env1["POSTGRES_PASSWORD"]  # DB password preserved
    data2 = yaml.safe_load(res2.compose_path.read_text())
    assert "mail.isnex.eu:192.168.55.250" in data2["services"]["backend"]["extra_hosts"]
    assert "host.docker.internal:host-gateway" in data2["services"]["backend"]["extra_hosts"]


def test_rotate_secrets_forces_fresh(tmp_path):
    res1 = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE)
    env1 = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in res1.env_path.read_text().splitlines() if "=" in ln}
    res2 = P.provision_uat(
        "nex-asistent",
        "asistent",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        rotate_secrets=True,
    )
    assert res2.is_redeploy is False
    env2 = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in res2.env_path.read_text().splitlines() if "=" in ln}
    assert env2["POSTGRES_PASSWORD"] != env1["POSTGRES_PASSWORD"]  # rotated


# ---------- Loopback debug ports (CLI path) ----------


def test_loopback_base_port_binds_fe_be_db(tmp_path):
    res = _provision(tmp_path, "nex-asistent", "asistent", ASISTENT_COMPOSE, loopback_base_port=19500)
    data = yaml.safe_load(res.compose_path.read_text())
    assert data["services"]["frontend"]["ports"] == ["127.0.0.1:19500:80"]
    assert data["services"]["backend"]["ports"] == ["127.0.0.1:19600:8000"]
    assert data["services"]["postgres"]["ports"] == ["127.0.0.1:19700:5432"]
    # Non-FE/BE/DB services still get no host port.
    assert "ports" not in data["services"]["qdrant"]


# ---------- 3-service render (CLI parity) + valid YAML ----------


def test_three_service_render_is_valid_and_labelled(tmp_path):
    res = _provision(tmp_path, "dev", "dev", THREE_SERVICE_COMPOSE)
    data = yaml.safe_load(res.compose_path.read_text())  # parses → valid compose YAML
    assert set(data["services"]) == {"db", "backend", "frontend"}
    assert res.db_service == "db"
    assert "traefik.http.routers.uat-dev.rule=Host(`uat-dev.isnex.eu`)" in data["services"]["frontend"]["labels"]
    assert (
        "traefik.http.routers.uat-dev-api.rule=Host(`uat-dev.isnex.eu`) && PathPrefix(`/api`)"
        in data["services"]["backend"]["labels"]
    )


# ---------- Role detection by convention (non-standard names) ----------


def test_identify_roles_by_image_and_build_not_just_name(tmp_path):
    compose = textwrap.dedent(
        """
        services:
          api:
            build:
              context: .
              dockerfile: backend/Dockerfile
            ports: ["8000:8000"]
          web:
            image: nginx:1.27-alpine
            ports: ["80:80"]
          store:
            image: postgres:16-alpine
        """
    )
    roles = P.identify_service_roles(yaml.safe_load(compose)["services"])
    assert roles["backend"] == "api"  # build points at backend/
    assert roles["frontend"] == "web"  # nginx image
    assert roles["db"] == "store"  # postgres image


# ---------- provision_uat guards + warnings ----------


def test_provision_warns_when_no_frontend_service(tmp_path):
    """The REAL nex-asistent shape (no FE service) → render succeeds but warns (no default route)."""
    no_fe = textwrap.dedent(
        """
        services:
          backend:
            build: { context: . }
            ports: ["8000:8000"]
          postgres:
            image: postgres:16-alpine
        """
    )
    res = _provision(tmp_path, "nex-asistent", "asistent", no_fe)
    assert res.fe_service is None
    assert any("no frontend service" in w for w in res.warnings)


def test_provision_missing_project_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        P.provision_uat("ghost", "ghost", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")


def test_provision_missing_source_compose_raises(tmp_path):
    (tmp_path / "projects" / "dev").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        P.provision_uat("dev", "dev", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")


# ---------- health-path resolution ----------


def test_resolve_health_path_from_compose_healthcheck(tmp_path):
    svc = {"healthcheck": {"test": ["CMD", "curl", "-sf", "http://localhost:8000/api/v1/health"]}}
    assert P.resolve_be_health_path(svc, tmp_path) == "/api/v1/health"


def test_resolve_health_path_from_dockerfile(tmp_path):
    project = tmp_path / "proj"
    (project / "backend").mkdir(parents=True)
    (project / "backend" / "Dockerfile").write_text(
        "FROM x\nHEALTHCHECK CMD curl -sf http://127.0.0.1:8000/api/v1/health || exit 1\n"
    )
    svc = {"build": {"context": ".", "dockerfile": "backend/Dockerfile"}}
    assert P.resolve_be_health_path(svc, project) == "/api/v1/health"


def test_resolve_health_path_defaults(tmp_path):
    assert P.resolve_be_health_path({}, tmp_path) == "/health"
    assert P.resolve_be_health_path(None, tmp_path) == "/health"


# ---------- CR-2: teardown_uat + reclaim_port (project-delete orphan prevention) ----------


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_teardown_uat_compose_present_runs_down_and_reclaims(tmp_path, monkeypatch):
    import json

    uat_root = tmp_path / "uat"
    (uat_root / "asistent").mkdir(parents=True)
    (uat_root / "asistent" / "docker-compose.yml").write_text("name: uat-asistent\nservices: {}\n")
    state = tmp_path / ".uat-ports.json"
    state.write_text('{"asistent": 19500, "other": 19501}')

    captured = {}

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc(returncode=0)

    monkeypatch.setattr(P.subprocess, "run", _run)
    ok, detail = P.teardown_uat("asistent", uat_root=uat_root, port_state_file=state)

    assert ok is True and detail == "OK"
    assert captured["cmd"][:3] == ["docker", "compose", "-f"]
    assert "down" in captured["cmd"] and "-v" in captured["cmd"]
    assert str(uat_root / "asistent" / "docker-compose.yml") in captured["cmd"]
    remaining = json.loads(state.read_text())
    assert "asistent" not in remaining and remaining == {"other": 19501}  # only this slug's port reclaimed


def test_teardown_uat_compose_absent_is_noop_but_reclaims_port(tmp_path, monkeypatch):
    import json

    uat_root = tmp_path / "uat"  # no compose at all
    state = tmp_path / ".uat-ports.json"
    state.write_text('{"gone": 19500}')

    def _run(*a, **kw):
        raise AssertionError("docker must NOT run when there is no compose")

    monkeypatch.setattr(P.subprocess, "run", _run)
    ok, detail = P.teardown_uat("gone", uat_root=uat_root, port_state_file=state)

    assert ok is True and "nothing to tear down" in detail
    assert json.loads(state.read_text()) == {}  # port still reclaimed


def test_teardown_uat_nonzero_returns_failure(tmp_path, monkeypatch):
    uat_root = tmp_path / "uat"
    (uat_root / "x").mkdir(parents=True)
    (uat_root / "x" / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **kw: _Proc(returncode=1, stdout="boom failure"))
    ok, detail = P.teardown_uat("x", uat_root=uat_root, port_state_file=tmp_path / ".p.json")
    assert ok is False and "exit 1" in detail and "boom failure" in detail


def test_teardown_uat_spawn_error_never_raises(tmp_path, monkeypatch):
    uat_root = tmp_path / "uat"
    (uat_root / "x").mkdir(parents=True)
    (uat_root / "x" / "docker-compose.yml").write_text("services: {}\n")

    def _boom(*a, **kw):
        raise OSError("docker binary missing")

    monkeypatch.setattr(P.subprocess, "run", _boom)
    ok, detail = P.teardown_uat("x", uat_root=uat_root, port_state_file=tmp_path / ".p.json")
    assert ok is False and "failed to run" in detail


def test_teardown_uat_invalid_slug_returns_false(tmp_path):
    ok, detail = P.teardown_uat("BAD/slug", uat_root=tmp_path)
    assert ok is False and "invalid uat_slug" in detail


def test_reclaim_port_removes_and_handles_missing(tmp_path):
    import json

    state = tmp_path / ".uat-ports.json"
    state.write_text('{"a": 19500, "b": 19501}')
    assert P.reclaim_port("a", port_state_file=state) is True
    assert json.loads(state.read_text()) == {"b": 19501}
    assert P.reclaim_port("missing", port_state_file=state) is False  # slug not present
    assert P.reclaim_port("a", port_state_file=tmp_path / "nope.json") is False  # no state file
