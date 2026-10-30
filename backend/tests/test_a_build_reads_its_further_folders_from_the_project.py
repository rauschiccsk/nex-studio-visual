"""DEV-269 — every folder a build reads points into the project, not into the installation's folder.

30.10.2026 the Director deployed NEX Knowledge 0.2.0 to ICC's UAT and the build refused: „additional privileges
requested: pass "--allow=fs.read=../opt/uat/icc/nex-knowledge" to grant requested privileges“. Its backend (like the
template's) reads two further folders of the project — ``additional_contexts: frontend: ./frontend, docs: ./docs``.
The render made ``context`` absolute and copied ``additional_contexts`` as they were; compose resolves a relative path
against the folder of the file it reads — the installation's — so it asked for ``/opt/uat/icc/nex-knowledge/frontend``,
which does not exist, outside the folder the build runs from. Measured the same way in the cockpit's container: the
relative form refused, the absolute one built. The verified-commit build redirected ``context`` only, so the further
folders also have to come from the exported commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from backend.services import uat_provisioner as P
from backend.services import verified_build as V

BACKEND = {
    "context": "./backend",
    "dockerfile": "Dockerfile",
    "additional_contexts": {"frontend": "./frontend", "docs": "./docs"},
}

#: NEX Knowledge's compose in its shape — the template's backend reads ./frontend and ./docs.
SOURCE = {
    "services": {
        "db": {"image": "pgvector/pgvector:pg16"},
        "migrate": {"build": dict(BACKEND), "command": ["alembic", "upgrade", "head"]},
        "backend": {"build": dict(BACKEND)},
        "worker": {"build": dict(BACKEND), "command": ["python", "-m", "app.worker"]},
        "frontend": {"build": {"context": "./frontend", "dockerfile": "Dockerfile"}},
        "docs-builder": {
            "build": {
                "context": "./docs",
                "additional_contexts": ["base=docker-image://python:3.12-slim", "app=service:backend", "kit=./kit"],
            },
            "profiles": ["docs"],
        },
    }
}


def _project(root: Path) -> Path:
    project = root / "projects" / "nex-knowledge"
    for folder in ("backend", "frontend", "docs", "kit"):
        (project / folder).mkdir(parents=True)
        (project / folder / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (project / "docker-compose.yml").write_text(yaml.safe_dump(SOURCE), encoding="utf-8")
    return project


def _folders_read(compose: dict, compose_dir: Path) -> dict[str, dict[str, Path]]:
    """Every folder each build reads — ``"."`` for its context, then its further folders by name — resolved the way
    compose resolves them: against the compose file's folder."""
    found: dict[str, dict[str, Path]] = {}
    for name, svc in compose["services"].items():
        build = svc.get("build")
        if not isinstance(build, dict):
            continue
        extra = build.get("additional_contexts") or {}
        pairs = extra.items() if isinstance(extra, dict) else (str(i).partition("=")[::2] for i in extra)
        paths = {".": build["context"], **{k: v for k, v in pairs if "://" not in v and not v.startswith("service:")}}
        found[name] = {k: (compose_dir / v).resolve() for k, v in paths.items()}
    return found


def _backend_folders(project: Path) -> dict[str, Path]:
    return {".": project / "backend", "frontend": project / "frontend", "docs": project / "docs"}


def test_a_new_installation_reads_every_build_folder_from_the_project(tmp_path):
    project = _project(tmp_path)
    compose = P.build_compose_for_instance(
        project_path=project,
        slug="icc-uat",
        project="nex-knowledge",
        environment="uat",
        customer_slug="icc",
        app="knowledge",
        version="0.2.0",
    )
    written = yaml.safe_load(P.render_uat_compose(compose))
    installation = tmp_path / "uat" / "icc" / "nex-knowledge"

    folders = _folders_read(written, installation)

    assert folders["backend"] == _backend_folders(project)
    assert folders["migrate"] == folders["worker"] == folders["backend"]
    assert folders["frontend"] == {".": project / "frontend"}
    assert all(f.is_dir() for paths in folders.values() for f in paths.values())
    # what is not a folder stays exactly as the project wrote it, in the project's form
    assert written["services"]["docs-builder"]["build"]["additional_contexts"] == [
        "base=docker-image://python:3.12-slim",
        "app=service:backend",
        f"kit={project / 'kit'}",
    ]


def test_the_next_deploy_into_the_installation_the_failed_one_wrote_reads_them_from_the_project(tmp_path):
    """NEX Knowledge's first deploy wrote the installation and failed on the build — the next one bumps the version
    into it, and the relative folders the old render copied must not survive that."""
    project = _project(tmp_path)
    broken = P.render_uat_compose(
        P.build_compose_for_instance(
            project_path=project,
            slug="icc-uat",
            project="nex-knowledge",
            environment="uat",
            customer_slug="icc",
            app="knowledge",
            version="0.2.0",
        )
    )
    as_written_on_30_10 = yaml.safe_load(broken)
    for name in ("migrate", "backend", "worker"):
        as_written_on_30_10["services"][name]["build"]["additional_contexts"] = {
            "frontend": "./frontend",
            "docs": "./docs",
        }

    after = P.version_bump_from_text(
        yaml.safe_dump(as_written_on_30_10),
        version="0.2.0",
        project_slug="nex-knowledge",
        source=SOURCE,
        project_path=project,
    )

    folders = _folders_read(after, tmp_path / "uat" / "icc" / "nex-knowledge")
    for name in ("migrate", "backend", "worker"):
        assert folders[name] == _backend_folders(project), name


def test_a_relative_context_is_resolved_as_a_path_not_stripped_of_its_dots(tmp_path):
    assert P._abs_build_context("./backend", tmp_path) == str(tmp_path / "backend")
    assert P._abs_build_context(".", tmp_path) == str(tmp_path)
    assert P._abs_build_context("../shared", tmp_path / "app") == str(tmp_path / "shared")
    assert P._abs_build_context(".docker", tmp_path) == str(tmp_path / ".docker")
    assert P._abs_build_context("/elsewhere/ctx", tmp_path) == "/elsewhere/ctx"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _committed(project: Path) -> str:
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "verified")
    return _git(project, "rev-parse", "HEAD")


def _installation(tmp_path: Path, project: Path) -> Path:
    compose = P.build_compose_for_instance(
        project_path=project,
        slug="icc-uat",
        project="nex-knowledge",
        environment="uat",
        customer_slug="icc",
        app="knowledge",
        version="0.2.0",
    )
    inst = tmp_path / "uat" / "icc" / "nex-knowledge"
    inst.mkdir(parents=True)
    path = inst / "docker-compose.yml"
    path.write_text(P.render_uat_compose(compose), encoding="utf-8")
    return path


def test_the_verified_build_reads_the_further_folders_from_the_exported_commit_too(tmp_path):
    project = _project(tmp_path)
    _committed(project)
    (project / "frontend" / "app.manifest.json").write_text("{} uncommitted", encoding="utf-8")

    prep = V.prepare(_installation(tmp_path, project))

    assert prep.note == "" and prep.build is not None
    src = prep.build.root / "src"
    override = yaml.safe_load(prep.build.override.read_text(encoding="utf-8"))["services"]
    for name in ("migrate", "backend", "worker"):
        assert override[name]["build"] == {
            "context": str(src / "backend"),
            "additional_contexts": {"frontend": str(src / "frontend"), "docs": str(src / "docs")},
        }, name
    # a folder that is not on the disk is left to the base compose; the one that is goes to the export
    assert override["docs-builder"]["build"]["additional_contexts"] == {"kit": str(src / "kit")}
    assert not (src / "frontend" / "app.manifest.json").exists(), "only what the commit tracks"
    V.cleanup(prep.build)


def test_a_further_folder_outside_the_repository_builds_as_before_and_says_so(tmp_path):
    project = _project(tmp_path)
    _committed(project)
    compose = _installation(tmp_path, project)
    data = yaml.safe_load(compose.read_text(encoding="utf-8"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    data["services"]["backend"]["build"]["additional_contexts"]["docs"] = str(elsewhere)
    compose.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert V.prepare(compose) == V.Preparation(
        None, "Stavba z pracovného priečinka — kontexty stavby neležia v jednom repozitári gitu."
    )
