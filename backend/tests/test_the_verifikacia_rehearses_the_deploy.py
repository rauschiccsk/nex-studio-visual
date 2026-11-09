"""DEV-329 — the Verifikácia rehearses the deploy with the deploy's own code before the version is Hotovo.

09.11.2026: NEX Websites 1.1.0 passed its Verifikácia and its deploy to ICC's UAT failed (DEV-328) — the cockpit
verified the project's compose and deployed an installation's compose nobody had run. Measured: the first UAT deploy
failed for 5 of 8 projects; 74 fixes went into the deploy path since June. Director: „Súhlasím“. Now the installation
the deploy would write — a new one and every existing UAT one — is written into a throwaway folder by
``provision_uat`` itself, built and started with an identity of its own; a failure is the Verifikácia's finding.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
import yaml

from backend.services import deploy_rehearsal as R
from backend.services import orchestrator as O
from backend.services import uat_provisioner as P
from backend.services import verified_build
from backend.services.deploy_rehearsal import rehearse as real_rehearse  # before the suite's stub replaces it

OLD = {
    "services": {
        "db": {"image": "postgres:16-alpine", "environment": {"POSTGRES_PASSWORD": "${DB_PASSWORD}"}},
        "migrate": {"build": {"context": "./backend"}, "command": ["alembic", "upgrade", "head"], "restart": "no"},
        "backend": {"build": {"context": "./backend"}, "ports": ["8000:8000"]},
        "frontend": {"build": {"context": "./frontend"}, "ports": ["8080:80"]},
    }
}
TEMPLATE_BUILD = {"context": "./backend", "additional_contexts": {"frontend": "./frontend", "docs": "./docs"}}
NEW = {
    "services": {
        **OLD["services"],
        "migrate": {**OLD["services"]["migrate"], "build": dict(TEMPLATE_BUILD)},
        "backend": {**OLD["services"]["backend"], "build": dict(TEMPLATE_BUILD)},
    }
}


def _project(root: Path, compose: dict) -> Path:
    project = root / "projects" / "nex-websites"
    for folder in ("backend", "frontend", "docs"):
        (project / folder).mkdir(parents=True, exist_ok=True)
        (project / folder / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (project / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
    (project / ".env.example").write_text("DB_PASSWORD=\nSECRET_KEY=\n", encoding="utf-8")
    return project


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """ICC's UAT installation of NEX Websites written by the 1.0 deploy, and the project gone onto the template."""
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    _project(tmp_path, OLD)
    uat = tmp_path / "uat"
    P.provision_uat(
        "nex-websites", "icc-uat", version="1.0.1", projects_root=tmp_path / "projects", uat_root=uat,
        prod_root=tmp_path / "customers", environment="uat", customer_slug="icc", app="websites",
        full_project_slug="nex-websites",
    )  # fmt: skip
    _project(tmp_path, NEW)
    return {"projects": tmp_path / "projects", "uat": uat, "work": tmp_path / "work"}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── what the rehearsal installs: the deploy's own render ───────────────────────────────────────────────────────────


def test_a_new_installation_and_every_existing_one_are_written_by_the_deploy_s_own_code(world):
    before = _snapshot(world["uat"])

    targets = R.render_targets(
        "nex-websites", "1.1.0", projects_root=world["projects"], uat_root=world["uat"], work=world["work"]
    )

    assert [t.label for t in targets] == [R.NEW_INSTALLATION, "inštalácia zákazníka icc"]
    existing = yaml.safe_load(targets[1].compose.read_text(encoding="utf-8"))["services"]
    project = world["projects"] / "nex-websites"
    # the deploy into the 1.0 installation — the one that failed on 09.11.2026 — builds the way the project does now
    assert existing["migrate"]["build"]["additional_contexts"] == {
        "frontend": str(project / "frontend"),
        "docs": str(project / "docs"),
    }
    assert all(t.compose.is_relative_to(world["work"]) and t.env.is_relative_to(world["work"]) for t in targets)
    real = P.secrets_from_env_text((world["uat"] / "icc" / "nex-websites" / ".env").read_text(encoding="utf-8"))
    rehearsal = targets[1].env.read_text(encoding="utf-8")
    assert [k for k, v in real.items() if v], "the 1.0 installation has secrets of its own"
    leaked = sorted(k for k, v in real.items() if v and v in rehearsal)
    assert leaked == [], "the customer's secrets never go into a rehearsal"
    assert _snapshot(world["uat"]) == before, "the real installation was not touched"


def test_an_installation_set_aside_is_not_rehearsed(world):
    (world["uat"] / "_archive" / "nex-websites").mkdir(parents=True)
    (world["uat"] / "_archive" / "nex-websites" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    targets = R.render_targets(
        "nex-websites", "1.1.0", projects_root=world["projects"], uat_root=world["uat"], work=world["work"]
    )

    assert [t.label for t in targets] == [R.NEW_INSTALLATION, "inštalácia zákazníka icc"]


# ── the rehearsal's own identity ───────────────────────────────────────────────────────────────────────────────────

INSTALLATION = {
    "name": "uat-icc-websites",
    "networks": {"nex-proxy-net": {"external": True}, "inbox-net": {"name": "uat-icc-websites-net"}},
    "volumes": {"db-data": {"name": "uat-icc-websites_db-data", "external": True}},
    "services": {
        "db": {
            "image": "postgres:16-alpine",
            "container_name": "uat-icc-websites-db",
            "volumes": ["db-data:/var/lib/pg"],
        },
        "backend": {
            "build": {"context": "/opt/projects/nex-websites/backend"},
            "image": "uat-icc-websites-backend:v1.1.0",
            "container_name": "uat-icc-websites-backend",
            "ports": ["127.0.0.1:18000:8000"],
            "networks": ["nex-proxy-net", "inbox-net"],
            "labels": [
                "traefik.enable=true",
                "traefik.http.routers.uat-icc-websites.rule=Host(`uat-icc-websites.isnex.eu`)",
            ],
            "env_file": [".env"],
            "volumes": [
                "/opt/uat/icc/nex-websites/data:/app/data",
                "./exports:/app/exports:rw",
                {"type": "bind", "source": "/mnt/genesis", "target": "/genesis"},
                "uploads:/app/uploads",
            ],
            "environment": {"APP_VERSION": "1.1.0"},
        },
    },
}


def test_the_rehearsal_has_an_identity_of_its_own_and_runs_what_the_installation_runs(tmp_path):
    env = tmp_path / ".env"

    out = R.isolated(INSTALLATION, "nex-websites-skuska-nasadenia-1", env)

    assert out["name"] == "nex-websites-skuska-nasadenia-1"
    assert out["networks"] == {"inbox-net": {}}, (
        "the outside network goes; the app's own stays, under the rehearsal's name"
    )
    assert out["volumes"] == {"db-data": {}}, "a volume's real name or external reaches the customer's data"
    backend = out["services"]["backend"]
    for key in ("container_name", "ports", "labels"):
        assert key not in backend, key
    assert backend["networks"] == ["inbox-net"]
    assert backend["image"] == "nex-websites-skuska-nasadenia-1-backend:skuska"
    assert backend["volumes"] == [
        "/app/data",
        "/app/exports",
        {"type": "volume", "target": "/genesis"},
        "uploads:/app/uploads",
    ]
    assert backend["env_file"] == [str(env)]
    # what it builds and runs stays
    assert backend["build"] == INSTALLATION["services"]["backend"]["build"]
    assert backend["environment"] == {"APP_VERSION": "1.1.0"}
    assert out["services"]["db"]["image"] == "postgres:16-alpine", "a foreign image is pulled as it is"
    assert "container_name" not in out["services"]["db"]
    assert INSTALLATION["services"]["backend"]["container_name"] == "uat-icc-websites-backend", (
        "the input is not changed"
    )


# ── one rehearsal: built, started, asked, taken down ───────────────────────────────────────────────────────────────


@pytest.fixture()
def docker(monkeypatch):
    """The smoke's steps as the rehearsal calls them — recorded; ``up`` answers what the test says."""
    calls: list[list[str]] = []
    state = {"up": (0, "Container started"), "ready": (True, "200")}

    async def step(cmd, timeout, env=None):
        calls.append(cmd)
        return state["up"] if "up" in cmd else (0, "")

    async def ready(base, service, port, **kw):
        calls.append(["ready", service, str(port), kw.get("path", "")])
        return state["ready"]

    monkeypatch.setattr(O, "_compose_smoke_step", step)
    monkeypatch.setattr(O, "_await_http_ready", ready)
    monkeypatch.setattr(verified_build, "prepare", lambda compose: verified_build.Preparation(None))

    async def throw_away(reference, env):
        calls.append(["remove-images", reference])

    monkeypatch.setattr(O, "throw_away_rehearsal_images", throw_away)
    return calls, state


def _target(tmp_path: Path) -> R.Rendered:
    folder = tmp_path / "inst"
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(yaml.safe_dump(INSTALLATION), encoding="utf-8")
    (folder / ".env").write_text("SECRET_KEY=x\n", encoding="utf-8")
    return R.Rendered("inštalácia zákazníka icc", folder / "docker-compose.yml", folder / ".env")


async def test_a_build_that_fails_is_the_finding_and_the_rehearsal_is_taken_down(tmp_path, docker):
    calls, state = docker
    state["up"] = (1, "failed to solve: frontend: pull access denied")

    ok, why = await R._run_one(_target(tmp_path), "nex-websites-skuska-nasadenia-1", tmp_path, "1.1.0")

    assert not ok and "failed to solve: frontend: pull access denied" in why
    assert any(c[-len(O._SMOKE_DOWN) :] == O._SMOKE_DOWN for c in calls), "taken down"
    assert ["remove-images", "nex-websites-skuska-nasadenia-1-*:skuska"] in calls, "only the rehearsal's own images"
    up = next(c for c in calls if "up" in c)
    assert up[up.index("-p") + 1] == "nex-websites-skuska-nasadenia-1"
    written = yaml.safe_load((tmp_path / "inst" / "skuska-nasadenia.yml").read_text(encoding="utf-8"))
    assert "container_name" not in written["services"]["backend"], "it ran the isolated compose, not the installation's"


async def test_a_backend_that_does_not_answer_is_the_finding(tmp_path, docker):
    calls, state = docker
    state["ready"] = (False, "connection refused")

    ok, why = await R._run_one(_target(tmp_path), "n-1", tmp_path, "1.1.0")

    assert (ok, why) == (False, "backend neodpovedá (connection refused)")
    assert ["ready", "backend", "8000", "/health"] in calls


async def test_a_rehearsal_that_runs_and_answers_passes(tmp_path, docker):
    assert await R._run_one(_target(tmp_path), "n-1", tmp_path, "1.1.0") == (True, "")


# ── the whole rehearsal says which installation failed ─────────────────────────────────────────────────────────────


async def test_the_finding_names_the_installation_that_failed(world, monkeypatch):
    async def run_one(target, name, project_path, version):
        return (False, "inštalácia sa nepostavila alebo nespustila:\nX") if "icc" in target.label else (True, "")

    monkeypatch.setattr(R, "_run_one", run_one)

    ok, sentence = await real_rehearse("nex-websites", "1.1.0", projects_root=world["projects"], uat_root=world["uat"])

    assert not ok
    assert (
        sentence
        == "Skúška nasadenia (inštalácia zákazníka icc) zlyhala — inštalácia sa nepostavila alebo nespustila:\nX"
    )


async def test_a_rehearsal_that_passes_says_what_it_did_not_check(world, monkeypatch):
    async def run_one(target, name, project_path, version):
        return True, ""

    monkeypatch.setattr(R, "_run_one", run_one)

    ok, sentence = await real_rehearse("nex-websites", "1.1.0", projects_root=world["projects"], uat_root=world["uat"])

    assert ok and sentence.startswith("Skúška nasadenia prešla (nová inštalácia, inštalácia zákazníka icc)")
    assert "Neoverené sú dáta zákazníka, stroj MAGER a prepojenie s NEX Managerom zákazníka." in sentence


async def test_an_installation_the_cockpit_cannot_write_is_the_finding_never_an_exception(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise ValueError("projekt nemá docker-compose.yml")

    monkeypatch.setattr(R, "render_targets", broken)

    ok, sentence = await real_rehearse("nex-x", "1.0.0", projects_root=tmp_path, uat_root=tmp_path)

    assert (ok, sentence) == (
        False,
        "Skúška nasadenia: kokpit nevedel zapísať predpis inštalácie (projekt nemá docker-compose.yml).",
    )


# ── the Verifikácia: a failed rehearsal fails the smoke ────────────────────────────────────────────────────────────


@pytest.fixture()
def smoke(monkeypatch):
    @contextlib.asynccontextmanager
    async def stack(project_slug, compose, roles):
        yield object()

    state = {
        "acceptance": (True, "akceptácia prešla", False),
        "rehearsal": (True, "Skúška nasadenia prešla."),
        "asked": [],
    }

    async def rehearse(project_slug, version, **_kw):
        state["asked"].append((project_slug, version))
        return state["rehearsal"]

    async def boot(stack_):
        return True, "aplikácia odpovedá"

    async def gate(*_a):
        return True, ""

    async def acceptance(*_a):
        return state["acceptance"]

    monkeypatch.setattr(O, "_smoke_compose_target", lambda slug: (Path("/x/docker-compose.yml"), {}, None))
    monkeypatch.setattr(O, "_boot_smoke_stack", stack)
    monkeypatch.setattr(O, "_boot_leg", boot)
    monkeypatch.setattr(O, "_run_aktualizacie_gate", gate)
    monkeypatch.setattr(O, "_run_release_acceptance", acceptance)
    monkeypatch.setattr(R, "rehearse", rehearse)
    return state


async def test_a_failed_rehearsal_fails_the_smoke_with_its_sentence(smoke):
    smoke["rehearsal"] = (False, "Skúška nasadenia (inštalácia zákazníka icc) zlyhala — X")

    (ok, detail), acceptance = await O._run_release_smoke("nex-websites", "1.1.0", (0, 0))

    assert (ok, detail) == (False, "Skúška nasadenia (inštalácia zákazníka icc) zlyhala — X")
    assert acceptance == (True, "akceptácia prešla", False)
    assert smoke["asked"] == [("nex-websites", "1.1.0")]


async def test_a_passed_rehearsal_is_said_beside_the_boot(smoke):
    (ok, detail), _ = await O._run_release_smoke("nex-websites", "1.1.0", (0, 0))

    assert (ok, detail) == (True, "aplikácia odpovedá Skúška nasadenia prešla.")


async def test_no_rehearsal_when_the_acceptance_failed(smoke):
    smoke["acceptance"] = (False, "akceptácia zlyhala", False)

    (ok, _detail), acceptance = await O._run_release_smoke("nex-websites", "1.1.0", (0, 0))

    assert ok and acceptance[0] is False
    assert smoke["asked"] == []


#: DEV-339 — Author Asistent's own networks: the AI worker reaches out only through the egress gateway.
SEALED = {
    "networks": {
        "app": {},
        "ai_internal": {"internal": True, "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}},
        "egress_out": {"ipam": {"config": [{"subnet": "10.240.7.0/24"}]}},
        "nex-proxy-net": {"external": True},
    },
    "services": {
        "backend": {"build": ".", "networks": ["app", "ai_internal", "nex-proxy-net"]},
        "worker": {"build": ".", "networks": {"ai_internal": {"ipv4_address": "10.240.6.5"}}},
        "egress": {"image": "ubuntu/squid", "networks": ["ai_internal", "egress_out"]},
        "frontend": {"build": ".", "networks": ["nex-proxy-net"]},
    },
}


def test_the_app_s_own_networks_stay_and_only_the_outside_one_goes(tmp_path):
    """09.11.2026 the rehearsal took every network away and Author Asistent's backend, which checks that its AI worker
    is sealed off, called itself unhealthy — a failure the real deploy does not have."""
    out = R.isolated(SEALED, "author-asistent-skuska-nasadenia-0", tmp_path / ".env")

    assert out["networks"] == {
        "app": {},
        "ai_internal": {"internal": True, "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}},
        "egress_out": {},  # a fixed subnet would collide with the installation's own
    }
    services = out["services"]
    assert services["backend"]["networks"] == ["app", "ai_internal"]
    assert services["worker"]["networks"] == {"ai_internal": {}}, "a fixed address belongs to the installation"
    assert services["egress"]["networks"] == ["ai_internal", "egress_out"]
    assert "networks" not in services["frontend"], "only the outside network was named — the default one serves"


def test_a_required_customer_value_gets_the_smoke_s_test_value(tmp_path, monkeypatch):
    """DEV-339 — Author Asistent's first account comes from required customer values the Manažér enters at a real first
    install (AUTHOR_USERNAME, AUTHOR_INITIAL_PASSWORD). The rehearsal had nobody to enter them, the backend stopped at
    „The account does not exist yet“ — it now gives them the smoke's test values (one rule, ``_smoke_contract_value``).
    """
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = _project(tmp_path, NEW)
    (project / ".env.example").write_text(
        "DB_PASSWORD=\nSECRET_KEY=\n"
        "# @customer required: „Prihlasovacie meno“\nAUTHOR_USERNAME=\n\n"
        "# @customer-secret required: „Počiatočné heslo“\nAUTHOR_INITIAL_PASSWORD=\n\n"
        "# @customer: „Nepovinné“\nEGRESS_EXTRA_DENY=\n",
        encoding="utf-8",
    )

    targets = R.render_targets(
        "nex-websites", "1.1.0", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", work=tmp_path / "w"
    )

    env = P._parse_env_file(targets[0].env)
    assert env["AUTHOR_USERNAME"] == O.SMOKE_CUSTOMER_VALUE
    assert len(env["AUTHOR_INITIAL_PASSWORD"]) >= 32, "a secret gets a random value by UAT's own rule"
    assert not env.get("EGRESS_EXTRA_DENY"), "an optional customer value stays as a real first install leaves it"
