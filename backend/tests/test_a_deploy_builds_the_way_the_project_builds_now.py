"""DEV-328 — a deploy into an existing installation builds every service the way the project builds it NOW.

09.11.2026 NEX Websites 1.1.0 passed its Verifikácia and the deploy to ICC's UAT failed on the build: „target migrate:
failed to solve: frontend: failed to resolve source metadata for docker.io/library/frontend:latest“. Going onto the
app template had given its backend a new build — two further folders, ``frontend`` and ``docs`` — and the project
built so in CI and in the Verifikácia. The installation was written by the 1.0 deploy, and the next deploy
(``version_bump_from_text``) bumped only the image tags: a service that already had a ``build`` kept the one from the
first deploy, so a change of the build in the project never reached the installation (measured: three builds and no
``additional_contexts`` in its compose). The Director to the fix — the project is the one source of how a service is
built; the installation keeps what is its own (variables and secrets, data volumes, ports, names): „Súhlasím“.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from backend.services import uat_provisioner as P
from backend.services import verified_build as V

#: NEX Websites 1.0.x — the backend and its migration step built from ./backend alone.
OLD_SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {"build": {"context": "./backend"}, "command": ["alembic", "upgrade", "head"], "restart": "no"},
        "backend": {"build": {"context": "./backend"}},
        "frontend": {"build": {"context": "./frontend"}},
    }
}

TEMPLATE_BUILD = {
    "context": "./backend",
    "dockerfile": "Dockerfile",
    "additional_contexts": {"frontend": "./frontend", "docs": "./docs"},
}

#: NEX Websites 1.1.0 on the app template — the backend reads ./frontend and ./docs too.
NEW_SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {"build": dict(TEMPLATE_BUILD), "command": ["alembic", "upgrade", "head"], "restart": "no"},
        "backend": {"build": dict(TEMPLATE_BUILD)},
        "frontend": {"build": {"context": "./frontend", "dockerfile": "Dockerfile"}},
    }
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _project(root: Path, source: dict) -> Path:
    project = root / "projects" / "nex-websites"
    for folder in ("backend", "frontend", "docs"):
        (project / folder).mkdir(parents=True, exist_ok=True)
        (project / folder / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (project / "docker-compose.yml").write_text(yaml.safe_dump(source), encoding="utf-8")
    return project


def _installation_of_1_0(root: Path, project: Path) -> Path:
    """The installation as the 1.0 deploy wrote it — rendered from the project before it went onto the template."""
    compose = P.build_compose_for_instance(
        project_path=project,
        slug="icc-uat",
        project="nex-websites",
        environment="uat",
        customer_slug="icc",
        app="websites",
        version="1.0.1",
    )
    inst = root / "uat" / "icc" / "nex-websites"
    inst.mkdir(parents=True)
    (inst / "docker-compose.yml").write_text(P.render_uat_compose(compose), encoding="utf-8")
    return inst


def _deploy_1_1_0(inst: Path, project: Path) -> dict:
    return P.version_bump_from_text(
        (inst / "docker-compose.yml").read_text(encoding="utf-8"),
        version="1.1.0",
        project_slug="nex-websites",
        source=NEW_SOURCE,
        project_path=project,
    )


def _template_build(project: Path) -> dict:
    return {
        "context": str(project / "backend"),
        "dockerfile": "Dockerfile",
        "additional_contexts": {"frontend": str(project / "frontend"), "docs": str(project / "docs")},
    }


def test_the_next_deploy_builds_the_backend_and_its_step_the_way_the_project_builds_now(tmp_path):
    project = _project(tmp_path, OLD_SOURCE)
    inst = _installation_of_1_0(tmp_path, project)
    _project(tmp_path, NEW_SOURCE)

    after = _deploy_1_1_0(inst, project)

    assert after["services"]["backend"]["build"] == _template_build(project)
    assert after["services"]["migrate"]["build"] == _template_build(project)
    assert after["services"]["frontend"]["build"] == {"context": str(project / "frontend"), "dockerfile": "Dockerfile"}


def test_what_belongs_to_the_installation_stays_its_own(tmp_path):
    project = _project(tmp_path, OLD_SOURCE)
    inst = _installation_of_1_0(tmp_path, project)
    before = yaml.safe_load((inst / "docker-compose.yml").read_text(encoding="utf-8"))
    _project(tmp_path, NEW_SOURCE)

    after = _deploy_1_1_0(inst, project)

    assert after.get("name") == before.get("name")
    assert after.get("networks") == before.get("networks") and after.get("volumes") == before.get("volumes")
    assert set(after["services"]) == set(before["services"])
    for name, svc in before["services"].items():
        kept = {k: v for k, v in svc.items() if k not in ("build", "image", "healthcheck")}
        assert {k: v for k, v in after["services"][name].items() if k in kept} == kept, name
    assert "build" not in after["services"]["db"], "a foreign image never gains a build"


def test_a_service_the_project_does_not_build_keeps_the_build_it_has(tmp_path):
    project = _project(tmp_path, OLD_SOURCE)
    inst = _installation_of_1_0(tmp_path, project)
    compose = yaml.safe_load((inst / "docker-compose.yml").read_text(encoding="utf-8"))
    compose["services"]["reports"] = {"build": {"context": "/opt/elsewhere/reports"}, "image": "icc-reports:1"}
    (inst / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
    _project(tmp_path, NEW_SOURCE)

    after = _deploy_1_1_0(inst, project)

    assert after["services"]["reports"]["build"] == {"context": "/opt/elsewhere/reports"}


def test_the_build_of_the_deploy_reads_the_further_folders_from_the_verified_commit(tmp_path):
    """The link, not its surroundings: the bumped installation goes to the verified-commit build, which is where
    09.11.2026 the step ``migrate`` asked for an image ``frontend``."""
    project = _project(tmp_path, OLD_SOURCE)
    inst = _installation_of_1_0(tmp_path, project)
    _project(tmp_path, NEW_SOURCE)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "1.1.0")
    (inst / "docker-compose.yml").write_text(yaml.safe_dump(_deploy_1_1_0(inst, project)), encoding="utf-8")

    prep = V.prepare(inst / "docker-compose.yml")

    assert prep.note == "" and prep.build is not None
    src = prep.build.root / "src"
    override = yaml.safe_load(prep.build.override.read_text(encoding="utf-8"))["services"]
    for name in ("migrate", "backend"):
        assert override[name]["build"] == {
            "context": str(src / "backend"),
            "additional_contexts": {"frontend": str(src / "frontend"), "docs": str(src / "docs")},
        }, name
    V.cleanup(prep.build)
