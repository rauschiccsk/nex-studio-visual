"""DEV-139 — a deploy builds its images from the verified commit, never from the project's working folder.

17.10.2026 NEX Inbox 1.9.0 to MÁGERSTAV's PROD (MAGER) choked ANDROS's line for ~15 minutes: ``docker compose build``
sent the whole working folder (~200 MB after .dockerignore, 164 MB of it the agent's npm cache) once per building
service — three times — and files git does not track went to the customer's machine. The tracked tree is 24 MB.
Director: „Súhlasím“ (kontext len z overeného commitu, každý obraz raz).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import yaml

from backend.services import orchestrator
from backend.services import verified_build as V
from backend.tests import _fake_backup_docker as fake_backup
from backend.tests.test_a_deploy_elsewhere_leaves_the_target_consistent import _asked_urls, _instance, _Proc


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """A project repo as the agent leaves it: committed code, plus its caches, a private file and an edit."""
    repo = tmp_path / "projects" / "nex-inbox"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "backend" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (repo / "frontend" / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("VERSION = 'committed'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "verified")
    commit = _git(repo, "rev-parse", "HEAD")
    (repo / ".npmcache").mkdir()
    (repo / ".npmcache" / "huge.tgz").write_bytes(b"x" * 4096)
    (repo / "private").mkdir()
    (repo / "private" / "faktura.pdf").write_bytes(b"%PDF customer")
    (repo / "backend" / "app.py").write_text("VERSION = 'uncommitted edit'\n", encoding="utf-8")
    return repo, commit


def _compose(dir_: Path, repo: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    compose = dir_ / "docker-compose.yml"
    backend = {"context": str(repo), "dockerfile": "backend/Dockerfile"}
    compose.write_text(
        yaml.safe_dump(
            {
                "name": "mager-inbox",
                "services": {
                    "postgres": {"image": "postgres:16-alpine"},
                    "alembic-init": {"image": "nex-inbox-backend:v1.9.0", "build": dict(backend)},
                    "backend": {"image": "nex-inbox-backend:v1.9.0", "build": dict(backend)},
                    "frontend": {
                        "image": "nex-inbox-frontend:v1.9.0",
                        "build": {"context": str(repo), "dockerfile": "frontend/Dockerfile"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return compose


def test_only_the_committed_tree_reaches_the_build_and_each_image_is_built_once(tmp_path):
    repo, commit = _repo(tmp_path)
    compose = _compose(tmp_path / "customers" / "mager" / "nex-inbox", repo)

    prep = V.prepare(compose)

    assert prep.note == ""
    build = prep.build
    assert build is not None and build.commit == commit
    src = build.root / "src"
    assert sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()) == [
        "backend/Dockerfile",
        "backend/app.py",
        "frontend/Dockerfile",
    ], "no cache, no private file — only what the commit tracks"
    assert (src / "backend" / "app.py").read_text(encoding="utf-8") == "VERSION = 'committed'\n"
    assert build.override.read_text(encoding="utf-8") == (
        "services:\n"
        '  "alembic-init":\n'
        "    build:\n"
        f'      context: "{src}"\n'
        '  "backend":\n'
        "    build: !reset null\n"
        '  "frontend":\n'
        "    build:\n"
        f'      context: "{src}"\n'
    )
    assert (build.images, build.reused) == (2, ("backend",))
    assert build.note() == (
        f"Stavba z overeného commitu {commit[:10]} (1 kB, stavaných obrazov: 2; obraz z inej služby použije backend)."
    )

    V.cleanup(build)
    assert not build.root.exists()


def test_contexts_in_two_repositories_build_as_before_and_say_so(tmp_path):
    repo, _commit = _repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    compose = _compose(tmp_path / "inst", repo)
    data = yaml.safe_load(compose.read_text(encoding="utf-8"))
    data["services"]["frontend"]["build"]["context"] = str(other)
    compose.write_text(yaml.safe_dump(data), encoding="utf-8")

    prep = V.prepare(compose)

    assert prep == V.Preparation(
        None, "Stavba z pracovného priečinka — kontexty stavby neležia v jednom repozitári gitu."
    )


def test_an_installation_that_builds_nothing_needs_no_preparation(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    compose = inst / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": {"web": {"image": "nginx:1.27"}}}), encoding="utf-8")

    assert V.prepare(compose) == V.Preparation(None, "")


def test_a_prod_deploy_builds_from_the_verified_commit_and_cleans_up(tmp_path, monkeypatch):
    """Through the real deploy: the build command carries the override, APP_COMMIT is the exported commit (not
    „-dirty“ from the working folder), the report says what was built, and the exported tree is gone afterwards."""
    instance_compose = _instance(tmp_path, monkeypatch)
    repo, commit = _repo(tmp_path / "work")
    instance_compose.write_text(_compose(tmp_path / "work" / "c", repo).read_text(encoding="utf-8"), encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    async def _spawn(*cmd, stdin=None, stdout=None, stderr=None, env=None):
        cmd = list(cmd)
        calls.append((cmd, dict(env or {})))
        if cmd[-1].endswith("/target/docker-compose.yml"):
            return _Proc(0, instance_compose.read_text(encoding="utf-8"))
        answer = fake_backup.reply(cmd)
        return _Proc(0, answer if answer is not None else "port 8000: 200")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _spawn)
    _asked_urls(monkeypatch)

    result = asyncio.run(
        orchestrator._run_prod_deploy(
            "nex-inbox", "mager", "inbox", "nex-inbox", version_number="1.9.0", deploy_host="mager"
        )
    )

    builds = [(cmd, env) for cmd, env in calls if cmd[-1] == "build" and cmd[:2] == ["docker", "compose"]]
    assert len(builds) == 1, builds
    cmd, env = builds[0]
    assert cmd[:4] == ["docker", "compose", "-f", str(instance_compose)]
    assert cmd[4] == "-f" and cmd[5].endswith("/verified-build.override.yml"), cmd
    assert env.get("APP_COMMIT") == commit
    assert f"Stavba z overeného commitu {commit[:10]} (" in result[1], result[1]
    assert not Path(cmd[5]).parent.exists(), "the exported commit stays on the cockpit's disk"
