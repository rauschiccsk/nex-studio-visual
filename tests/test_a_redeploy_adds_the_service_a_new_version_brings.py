"""DEV-304 — a service a new version brings goes into the installation, under the installation's names.

04.11.2026, NEX Knowledge 0.2.1 on the UAT of ICC: the version brought ``data-owner``, a one-shot step that hands the
storage in ``/data`` to uid 1000 before the backend and the worker start (up to 0.2.0 the app ran as 10001). The
redeploy only raises the version of what an installation has (ICCINT-133), so the step never came; the deploy page
said „Projekt má služby, ktoré inštalácia nemá: data-owner … Ak ich tam treba, doplň ich do inštalácie“ — advice the
Manažér had nothing to press for. A new service now comes whole from what the cockpit writes into a new installation,
with the dependencies of the services that wait for it; nothing else of the installation changes. A service that
would need storage, a network or a service the installation does not have under the same name stays out, and the deploy
says why. Director 04.11.2026: „Súhlasím“.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

SOURCE_020 = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  backend:
    build:
      context: ./backend
    volumes:
      - files:/data
  worker:
    build:
      context: ./backend
    command: ["python", "-m", "app.worker"]
    volumes:
      - files:/data
  frontend:
    build:
      context: ./frontend
volumes:
  files: {}
"""
#: 0.2.1 brings the step that hands the storage to uid 1000, and the backend and the worker wait for it.
SOURCE_021 = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  data-owner:
    build:
      context: ./backend
    user: "0:0"
    command: ["sh", "-c", "find /data ! -user 1000 -exec chown -h 1000:1000 {} +"]
    restart: "no"
    volumes:
      - files:/data
  backend:
    build:
      context: ./backend
    volumes:
      - files:/data
    depends_on:
      data-owner:
        condition: service_completed_successfully
  worker:
    build:
      context: ./backend
    command: ["python", "-m", "app.worker"]
    volumes:
      - files:/data
    depends_on:
      data-owner:
        condition: service_completed_successfully
  frontend:
    build:
      context: ./frontend
volumes:
  files: {}
"""
EXAMPLE = "SECRET_KEY=change-me\n"
STEP = "data-owner"


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], capture_output=True, check=True)


def _project(tmp_path: Path, monkeypatch, *, tagged_020: str = SOURCE_020) -> Path:
    """The project with its release 0.2.0 tagged in git (*tagged_020*), its working tree at 0.2.0 as installed."""
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-knowledge"
    project.mkdir(parents=True)
    (project / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    (project / "docker-compose.yml").write_text(tagged_020, encoding="utf-8")
    _git(project.parent, "init", "-q", "-b", "main", str(project))
    _git(project, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "0")
    _git(project, "add", "-A")
    _git(project, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "release 0.2.0")
    _git(project, "tag", "v0.2.0")
    (project / "docker-compose.yml").write_text(SOURCE_020, encoding="utf-8")
    return project


def _deploy(tmp_path: Path, version: str):
    return P.provision_uat(
        "nex-knowledge",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="knowledge",
        full_project_slug="nex-knowledge",
        version=version,
    )


def _services(result) -> dict:
    return yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))["services"]


def _fresh(tmp_path: Path, version: str) -> dict:
    """What the cockpit writes into a NEW installation of the project at *version* — the reference for the step."""
    fresh_root = tmp_path / "fresh"
    result = P.provision_uat(
        "nex-knowledge",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=fresh_root,
        customer_slug="icc",
        app="knowledge",
        full_project_slug="nex-knowledge",
        version=version,
    )
    return _services(result)


def _without_step(services: dict) -> dict:
    """The installation's services with the step and the waiting for it taken out — what must stay as it was."""
    kept = {name: dict(svc) for name, svc in services.items() if name != STEP}
    for svc in kept.values():
        waits = svc.get("depends_on")
        if isinstance(waits, dict) and STEP in waits:
            svc["depends_on"] = {name: cond for name, cond in waits.items() if name != STEP} or None
            if svc["depends_on"] is None:
                del svc["depends_on"]
    return kept


def test_the_step_a_new_version_brings_comes_into_the_installation(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    _deploy(tmp_path, "0.2.0")
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")
    fresh = _fresh(tmp_path, "0.2.1")

    again = _deploy(tmp_path, "0.2.1")
    services = _services(again)

    assert STEP in services, again.warnings
    step, written = dict(services[STEP]), dict(fresh[STEP])
    assert step.pop("image").rsplit(":", 1)[1] == services["backend"]["image"].rsplit(":", 1)[1], (
        "the step's image is tagged like every service the redeploy raised"
    )
    written.pop("image")
    assert step == written, "the step as the cockpit writes it into a new installation"
    for waiting in ("backend", "worker"):
        assert services[waiting]["depends_on"][STEP] == {"condition": "service_completed_successfully"}
    assert any(STEP in w and "doplnené" in w for w in again.warnings), again.warnings
    assert not any("nedoplní" in w for w in again.warnings), again.warnings


def test_nothing_else_of_the_installation_changes(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    _deploy(tmp_path, "0.2.0")
    only_the_version = _services(_deploy(tmp_path, "0.2.1"))
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")

    with_the_step = _services(_deploy(tmp_path, "0.2.1"))

    assert _without_step(with_the_step) == only_the_version


def test_a_step_needing_storage_the_installation_names_otherwise_stays_out(tmp_path, monkeypatch):
    """An installation that keeps its storage under its own name (taken over, ICCINT-133) gets nothing it cannot run —
    the step would mount an empty new volume instead of the app's files."""
    project = _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "0.2.0")
    text = first.compose_path.read_text(encoding="utf-8")
    own = yaml.safe_load(text)
    volume = next(iter(own["volumes"]))
    own["volumes"] = {"nk_files": own["volumes"][volume]}
    for svc in own["services"].values():
        if "volumes" in svc:
            svc["volumes"] = [m.replace(f"{volume}:", "nk_files:", 1) for m in svc["volumes"]]
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    first.compose_path.write_text(header + yaml.safe_dump(own, sort_keys=False), encoding="utf-8")
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")

    again = _deploy(tmp_path, "0.2.1")
    services = _services(again)

    assert STEP not in services
    assert all(STEP not in (svc.get("depends_on") or {}) for svc in services.values())
    said = [w for w in again.warnings if STEP in w]
    assert said and all(volume in w for w in said), again.warnings


def test_an_installation_with_its_own_service_names_gets_nothing(tmp_path, monkeypatch):
    """A service the project calls otherwise would come a second time — a second worker over the same storage."""
    project = _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "0.2.0")
    text = first.compose_path.read_text(encoding="utf-8")
    own = yaml.safe_load(text)
    own["services"]["nk-worker"] = own["services"].pop("worker")
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    first.compose_path.write_text(header + yaml.safe_dump(own, sort_keys=False), encoding="utf-8")
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")

    again = _deploy(tmp_path, "0.2.1")
    services = _services(again)

    assert set(services) == set(own["services"]), "nothing came — neither the step nor a second worker"
    assert all(STEP not in (svc.get("depends_on") or {}) for svc in services.values())
    said = [w for w in again.warnings if STEP in w]
    assert said and all("nk-worker" in w for w in said), again.warnings


def _add(installation: dict, render: dict, missing: list[str]):
    return P.new_services_into_installation(
        installation, render, missing=missing, project_slug="nex-knowledge", version="0.2.1"
    )


def test_a_service_the_project_had_at_the_installed_version_stays_out(tmp_path, monkeypatch):
    """Missing although the project had it already at the version the installation runs — missing on purpose (a
    database kept elsewhere), not brought by the new version."""
    project = _project(tmp_path, monkeypatch, tagged_020=SOURCE_021)
    _deploy(tmp_path, "0.2.0")
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")

    again = _deploy(tmp_path, "0.2.1")

    assert STEP not in _services(again)
    said = [w for w in again.warnings if STEP in w]
    assert said and all("už vo verzii 0.2.0" in w for w in said), again.warnings


def test_without_the_projects_history_nothing_is_taken_for_new(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    _deploy(tmp_path, "0.2.0")
    subprocess.run(["rm", "-rf", str(project / ".git")], check=True)
    (project / "docker-compose.yml").write_text(SOURCE_021, encoding="utf-8")

    again = _deploy(tmp_path, "0.2.1")

    assert STEP not in _services(again)
    said = [w for w in again.warnings if STEP in w]
    assert said and all("nevie povedať" in w for w in said), again.warnings


def test_a_brought_service_with_a_foreign_image_is_pulled_not_refused(tmp_path, monkeypatch):
    """``redis:7`` is not on the server and nobody builds it — it is pulled, as on a first install."""
    project = _project(tmp_path, monkeypatch)
    _deploy(tmp_path, "0.2.0")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: not image.startswith("redis"))
    with_cache = yaml.safe_load(SOURCE_020)
    with_cache["services"]["cache"] = {"image": "redis:7"}
    (project / "docker-compose.yml").write_text(yaml.safe_dump(with_cache), encoding="utf-8")

    again = _deploy(tmp_path, "0.2.1")

    assert _services(again)["cache"]["image"] == "redis:7"


def _installation(**extra_services) -> dict:
    return {
        "services": {
            "db": {"image": "postgres:16-alpine"},
            "backend": {"image": "uat-icc-knowledge-backend:v0.2.1", "volumes": ["files:/data", "./exports:/exports"]},
            **extra_services,
        },
        "volumes": {"files": {}},
        "networks": {"nex-proxy-net": {"external": True}},
    }


def _render(step: dict, **waiting_for_it) -> dict:
    services = _installation()["services"] | {STEP: step}
    for name, condition in waiting_for_it.items():
        services[name] = dict(services.get(name) or {"image": f"x-{name}:0.2.1"}) | {"depends_on": {STEP: condition}}
    return {"services": services}


@pytest.mark.parametrize(
    "step, said",
    [
        ({"image": "s:0.2.1", "networks": ["backup-net"]}, "potrebuje sieť backup-net"),
        ({"image": "s:0.2.1", "volumes": ["./archive:/archive"]}, "pripája ./archive"),
        ({"image": "s:0.2.1", "volumes": [{"type": "volume", "source": "blobs", "target": "/b"}]}, "úložisko blobs"),
        ({"image": "s:0.2.1", "depends_on": ["redis"]}, "čaká na službu redis"),
    ],
    ids=["a network", "a folder", "a volume (long form)", "an awaited service"],
)
def test_a_step_the_installation_could_not_run_stays_out_and_says_why(step, said):
    installation = _installation()
    before = copy.deepcopy(installation)

    added, left_out = P.new_services_into_installation(
        installation, _render(step), missing=[STEP], project_slug="nex-knowledge", version="0.2.1"
    )

    assert added == {}
    assert said in left_out[STEP]
    assert installation == before


def test_what_the_installation_has_is_enough():
    """The same folder and network the installation already uses, a service it has — the step comes."""
    step = {"image": "s:0.2.1", "volumes": ["./exports:/exports"], "networks": ["nex-proxy-net"], "depends_on": ["db"]}
    installation = _installation()

    added, left_out = P.new_services_into_installation(
        installation, _render(step), missing=[STEP], project_slug="nex-knowledge", version="0.2.1"
    )

    assert (added, left_out) == ({STEP: []}, {})
    assert installation["services"][STEP]["volumes"] == ["./exports:/exports"]


def test_a_step_a_service_the_installation_lacks_waits_for_stays_out():
    installation = _installation()

    added, left_out = P.new_services_into_installation(
        installation,
        _render({"image": "s:0.2.1"}, cron={"condition": "service_completed_successfully"}),
        missing=[STEP],
        project_slug="nex-knowledge",
        version="0.2.1",
    )

    assert added == {}
    assert "čaká na ňu služba cron" in left_out[STEP]


def test_a_service_the_cockpit_does_not_write_stays_out():
    added, left_out = _add(_installation(), _render({"image": "s:0.2.1"}), ["mailer"])

    assert added == {}
    assert "nezapisuje" in left_out["mailer"]


def test_two_new_services_come_whichever_waits_for_the_other():
    render = _render({"image": "s:0.2.1", "depends_on": {"cache": {"condition": "service_healthy"}}})
    render["services"]["cache"] = {"image": "redis:7"}
    installation = _installation()

    added, left_out = P.new_services_into_installation(
        installation, render, missing=[STEP, "cache"], project_slug="nex-knowledge", version="0.2.1"
    )

    assert left_out == {}
    assert set(added) == {STEP, "cache"}
    assert installation["services"][STEP]["depends_on"] == {"cache": {"condition": "service_healthy"}}


def test_a_service_waiting_in_the_short_form_keeps_what_it_waited_for():
    installation = _installation(frontend={"image": "f:0.2.1", "depends_on": ["backend"]})
    render = _render({"image": "s:0.2.1"}, frontend={"condition": "service_completed_successfully"})
    render["services"]["frontend"]["depends_on"] = {
        "backend": {"condition": "service_started"},
        STEP: {"condition": "service_completed_successfully"},
    }

    _add(installation, render, [STEP])

    assert installation["services"]["frontend"]["depends_on"] == {
        "backend": {"condition": "service_started"},
        STEP: {"condition": "service_completed_successfully"},
    }
