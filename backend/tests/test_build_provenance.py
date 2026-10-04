"""Stavba obrazu dostane číslo zmeny, z ktorej sa stavia (ICCINT-166).

04.10.2026 sa dedo-home nedal zostaviť v skúške spustenia: pripína ku zmene zoznam zámkov aj dôkaz v obraze,
``.git`` do kontextu stavby nepatrí — a kokpit stavbe odovzdával len verziu. Číslo zmeny nikde, na žiadnom zo
štyroch miest, kde obraz projektu stavia.

Skúšky tu sú dvojaké. Prvé merajú samotné zistenie na SKUTOČNOM git repozitári — napodobenina gitu by potvrdila
len moju predstavu o ňom. Druhé overujú každé miesto stavby. A posledná je stráž v tvare pravidla: nájde v kóde
kokpitu každé miesto, ktoré stavia obraz, a pýta sa, či niektoré zostalo bez čísla zmeny. Piate miesto, ktoré
niekto raz pridá, tak nezostane slepé len preto, že ho nikto nepripísal do zoznamu.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services import create_project_postscaffold, orchestrator
from backend.services.build_provenance import APP_COMMIT_ENV, DIRTY_SUFFIX, build_commit, build_env

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _repo(root: Path, compose: str = "services:\n  backend:\n    build: ./backend\n") -> Path:
    """Projekt s compose, backendom, frontendom a dokumentáciou — všetko uložené v jednej zmene."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "docker-compose.yml").write_text(compose)
    for part in ("backend", "frontend", "docs"):
        (root / part).mkdir()
        (root / part / "main.txt").write_text(part)
    (root / ".gitignore").write_text(".env\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


# ── Zistenie samo ────────────────────────────────────────────────────────────────────────────────────────


def test_a_clean_build_context_gives_the_head_commit(tmp_path) -> None:
    repo = _repo(tmp_path / "p")
    head = _git(repo, "rev-parse", "HEAD")
    assert build_commit(repo / "docker-compose.yml") == head
    assert build_env(repo / "docker-compose.yml") == {APP_COMMIT_ENV: head}


def test_both_compose_build_forms_are_read(tmp_path) -> None:
    repo = _repo(
        tmp_path / "p",
        "services:\n"
        "  backend:\n    build: ./backend\n"
        "  frontend:\n    build:\n      context: ./frontend\n      dockerfile: Dockerfile\n"
        "  db:\n    image: postgres:16-alpine\n",
    )
    assert build_commit(repo / "docker-compose.yml") == _git(repo, "rev-parse", "HEAD")


def test_a_changed_file_in_the_context_marks_the_commit_dirty(tmp_path) -> None:
    repo = _repo(tmp_path / "p")
    (repo / "backend" / "main.txt").write_text("changed, not committed")
    assert build_commit(repo / "docker-compose.yml") == _git(repo, "rev-parse", "HEAD") + DIRTY_SUFFIX


def test_an_untracked_file_in_the_context_marks_it_dirty_too(tmp_path) -> None:
    """``COPY . .`` ho do obrazu vezme — obraz by tvrdil zmenu, ktorú neobsahuje."""
    repo = _repo(tmp_path / "p")
    (repo / "backend" / "new.py").write_text("x")
    assert build_commit(repo / "docker-compose.yml").endswith(DIRTY_SUFFIX)


def test_a_change_outside_the_build_context_does_not(tmp_path) -> None:
    repo = _repo(tmp_path / "p")
    (repo / "docs" / "main.txt").write_text("changed outside of what is built")
    assert build_commit(repo / "docker-compose.yml") == _git(repo, "rev-parse", "HEAD")


def test_an_ignored_file_in_the_context_does_not(tmp_path) -> None:
    repo = _repo(tmp_path / "p", "services:\n  backend:\n    build: .\n")
    (repo / ".env").write_text("SECRET=x")
    assert build_commit(repo / "docker-compose.yml") == _git(repo, "rev-parse", "HEAD")


def test_an_instance_compose_outside_the_repo_reads_the_repo_it_builds_from(tmp_path) -> None:
    """Nasadenie: compose leží v ``/opt/uat/<slug>``, kontexty stavby ukazujú absolútne do projektu."""
    repo = _repo(tmp_path / "projects" / "app")
    instance = tmp_path / "uat" / "app"
    instance.mkdir(parents=True)
    (instance / "docker-compose.yml").write_text(
        f"services:\n  backend:\n    build:\n      context: {repo / 'backend'}\n"
    )
    assert build_commit(instance / "docker-compose.yml") == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "why, compose",
    [
        ("nič sa nestavia", "services:\n  db:\n    image: postgres:16-alpine\n"),
        ("vzdialený kontext", "services:\n  backend:\n    build: https://github.com/x/y.git\n"),
        ("kontext neexistuje", "services:\n  backend:\n    build: ./nowhere\n"),
        ("pokazený compose", "services: [\n"),
    ],
)
def test_nothing_is_invented_when_it_cannot_be_said(tmp_path, why, compose) -> None:
    repo = _repo(tmp_path / "p")
    (repo / "docker-compose.yml").write_text(compose)
    assert build_commit(repo / "docker-compose.yml") is None, why
    assert build_env(repo / "docker-compose.yml") == {}, why


def test_contexts_in_two_repositories_are_not_one_commit(tmp_path) -> None:
    one = _repo(tmp_path / "one")
    two = _repo(tmp_path / "two")
    (one / "docker-compose.yml").write_text(
        f"services:\n  a:\n    build: ./backend\n  b:\n    build: {two / 'backend'}\n"
    )
    assert build_commit(one / "docker-compose.yml") is None


def test_a_nested_repository_is_not_the_outer_one(tmp_path) -> None:
    """Vonkajší ``git status`` do vnoreného repozitára nevidí a hlási „čisté" — číslo zmeny vonkajšieho by tak
    patrilo aj kódu, ktorý v nej vôbec nie je."""
    outer = _repo(tmp_path / "outer")
    _repo(outer / "vendor")
    (outer / "docker-compose.yml").write_text(
        "services:\n  a:\n    build: ./backend\n  b:\n    build: ./vendor/backend\n"
    )
    assert build_commit(outer / "docker-compose.yml") is None


def test_a_build_outside_any_repository_gets_nothing(tmp_path) -> None:
    plain = tmp_path / "plain"
    (plain / "backend").mkdir(parents=True)
    (plain / "docker-compose.yml").write_text("services:\n  backend:\n    build: ./backend\n")
    assert build_commit(plain / "docker-compose.yml") is None


def test_a_missing_compose_gets_nothing(tmp_path) -> None:
    assert build_commit(tmp_path / "docker-compose.yml") is None


# ── Každé miesto, kde kokpit stavia obraz ────────────────────────────────────────────────────────────────


class _Steps:
    """Zachytí každý ``docker compose`` krok skúšky spustenia aj s prostredím, ktoré dostal."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict | None]] = []

    async def __call__(self, cmd: list[str], timeout: int, env=None) -> tuple[int, str]:
        self.calls.append((cmd, env))
        return 0, "ok"

    def env_of(self, verb: str) -> dict | None:
        return next(env for cmd, env in self.calls if verb in cmd)


@pytest.mark.asyncio
async def test_the_release_smoke_builds_and_tears_down_with_the_commit(monkeypatch, tmp_path) -> None:
    """``up`` stavia — a ``down`` to isté prostredie dostane tiež: projekt, ktorý by premennú vyžadoval
    (``${APP_COMMIT:?}``), by sa inak postavil, spustil a už nezastavil."""
    repo = _repo(tmp_path / "p")
    steps = _Steps()
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", steps)

    async with orchestrator._boot_smoke_stack("p", repo / "docker-compose.yml", {}):
        pass

    head = _git(repo, "rev-parse", "HEAD")
    assert steps.env_of("up")[APP_COMMIT_ENV] == head
    assert steps.env_of("down")[APP_COMMIT_ENV] == head


@pytest.mark.asyncio
async def test_the_acceptance_script_resolves_the_compose_like_the_engine(monkeypatch, tmp_path) -> None:
    """Projektový skript previerky ovláda stack vlastným ``docker compose``. Musí ho interpolovať zo súboru, ktorým
    engine stack spustil, a s číslom zmeny — nie zo živého ``.env`` projektu (dedo-home 04.10.2026: dva riadky,
    osem povinných premenných chýbalo, skript by padol na prvom príkaze).

    Meria sa SKUTOČNÝM ``docker compose config`` — napodobenina by potvrdila len moju predstavu o ňom. ``config``
    sa démona nedotkne, a adresa Dockera je aj tak nasmerovaná nikam."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    repo = _repo(
        tmp_path / "p",
        "services:\n  backend:\n    build: ./backend\n    environment:\n"
        "      KEY: ${KEY:?chyba KEY}\n      COMMIT: ${APP_COMMIT:?chyba APP_COMMIT}\n",
    )
    (repo / ".env.example").write_text("KEY=dev-only\n")
    (repo / ".env").write_text("OTHER=1\n")  # živé nastavenie projektu — neúplné
    (repo / "release_smoke_test.sh").write_text(
        "set -e\n"
        'docker compose -p "$SMOKE_PROJECT" -f "$SMOKE_COMPOSE" -f "$SMOKE_OVERRIDE" config -q\n'
        "echo ASSERTIONS_RUN=1\necho FEATURE_ASSERTIONS_RUN=1\n"
    )
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _Steps())
    roles = {"backend": "backend", "frontend": None, "db": None}

    async with orchestrator._boot_smoke_stack("p", repo / "docker-compose.yml", roles) as stack:
        ok, detail, _skipped = await orchestrator._run_release_acceptance(stack, "p", (1, 0))

    assert ok, detail


@pytest.mark.asyncio
async def test_the_deploy_builds_with_the_commit_next_to_the_version(monkeypatch, tmp_path) -> None:
    repo = _repo(tmp_path / "projects" / "app")
    instance = tmp_path / "uat" / "app"
    instance.mkdir(parents=True)
    compose = instance / "docker-compose.yml"
    compose.write_text(f"services:\n  backend:\n    build:\n      context: {repo / 'backend'}\n")
    monkeypatch.setattr(orchestrator, "_uat_compose_path", lambda *a, **k: compose)

    seen: dict = {}

    async def _spawn(*cmd, env=None, **_k):
        seen["cmd"], seen["env"] = list(cmd), env
        raise OSError("zachytené, ďalej netreba")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _spawn)

    ok, _detail = await orchestrator._run_uat_deploy("app", "app", version_number="v1.2.3")

    assert ok is False  # zastavené zámerne pri spustení
    assert "--build" in seen["cmd"]
    assert seen["env"][APP_COMMIT_ENV] == _git(repo, "rev-parse", "HEAD")
    assert seen["env"]["APP_VERSION"] == "1.2.3"


def test_the_new_project_smoke_builds_starts_and_stops_with_the_commit(monkeypatch, tmp_path) -> None:
    repo = _repo(tmp_path / "p")
    calls: list[tuple[list[str], dict | None]] = []
    real_run = subprocess.run

    def _run(cmd, *a, env=None, **k):
        if cmd[0] == "git":  # ``subprocess`` je jeden modul pre celý proces — zistenie čísla zmeny ide naostro
            return real_run(cmd, *a, env=env, **k)
        calls.append((list(cmd), env))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(create_project_postscaffold.subprocess, "run", _run)

    create_project_postscaffold._run_smoke_test(repo, "p", full=True)

    head = _git(repo, "rev-parse", "HEAD")
    compose_calls = [(cmd, env) for cmd, env in calls if cmd[:2] == ["docker", "compose"]]
    assert {cmd[2] for cmd, _ in compose_calls} >= {"build", "up", "down"}
    for cmd, env in compose_calls:
        assert env is not None and env.get(APP_COMMIT_ENV) == head, cmd


# ── Stráž ako pravidlo ───────────────────────────────────────────────────────────────────────────────────

#: Čo v kóde znamená „tu sa stavia obraz": ``up … --build`` alebo ``compose build`` — v ktorejkoľvek podobe,
#: akou kokpit volá docker (zoznam argumentov, alebo pomocník ``docker_compose(["build"], …)``).
_BUILDS_AN_IMAGE = re.compile(r"""["']--build["']|["']compose["']\s*,\s*["']build["']|\(\s*\[\s*["']build["']""")


def _build_sites() -> dict[Path, list[int]]:
    sites: dict[Path, list[int]] = {}
    for folder in (REPO_ROOT / "backend" / "services", REPO_ROOT / "scripts"):
        for path in sorted(folder.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.lstrip().startswith("#") and _BUILDS_AN_IMAGE.search(line):
                    sites.setdefault(path, []).append(number)
    return sites


def test_the_guard_finds_the_build_sites_it_is_about() -> None:
    """Pravidlo, ktoré nič nenájde, nič nestráži. Všetky štyri miesta známe 04.10.2026 musí nájsť samo."""
    found = {path.relative_to(REPO_ROOT).as_posix() for path in _build_sites()}
    assert {
        "backend/services/orchestrator.py",
        "backend/services/create_project_postscaffold.py",
        "scripts/uat-deploy.py",
    } <= found
    assert sum(len(lines) for lines in _build_sites().values()) >= 4


def test_every_place_that_builds_an_image_hands_it_the_commit() -> None:
    """Súbor, ktorý stavia obraz projektu, musí číslo zmeny brať z jedného miesta (``build_provenance``)."""
    blind = [
        f"{path.relative_to(REPO_ROOT)}:{lines}"
        for path, lines in _build_sites().items()
        if "build_provenance.build_env" not in path.read_text(encoding="utf-8")
    ]
    assert not blind, "stavia obraz, ale neodovzdá mu číslo zmeny (ICCINT-166): " + ", ".join(blind)


def test_the_manual_uat_script_builds_with_the_commit(monkeypatch, tmp_path) -> None:
    """Ručný ``uat-deploy.py`` — štvrté miesto. Načíta sa ako skript, rovnako ako ho púšťa človek."""
    spec = importlib.util.spec_from_file_location("uat_deploy_iccint166", REPO_ROOT / "scripts" / "uat-deploy.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "path", [str(REPO_ROOT / "scripts"), str(REPO_ROOT), *sys.path])
    spec.loader.exec_module(mod)

    projects = tmp_path / "projects"
    repo = _repo(
        projects / "dev",
        "services:\n"
        "  backend:\n    build:\n      context: .\n      dockerfile: backend/Dockerfile\n    ports: ['8000:8000']\n"
        "  frontend:\n    build:\n      context: ./frontend\n    ports: ['3000:80']\n",
    )
    uat_root = tmp_path / "uat"
    uat_root.mkdir()
    monkeypatch.setattr(mod, "UAT_ROOT", uat_root)
    monkeypatch.setattr(mod, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")

    calls: list[tuple[list[str], dict | None]] = []
    monkeypatch.setattr(mod._uat_lib, "docker_compose", lambda args, **k: calls.append((args, k.get("env"))))
    monkeypatch.setattr(mod._uat_lib, "docker_exec", lambda *a, **k: None)
    monkeypatch.setattr(mod._uat_lib, "wait_healthy", lambda *a, **k: True)

    assert mod.deploy("dev", project=None, dry_run=False, version="v0.2.0") == 0

    build_calls = [env for args, env in calls if args[:1] == ["build"]]
    assert build_calls, "skript nič nestaval"
    assert build_calls[0][APP_COMMIT_ENV] == _git(repo, "rev-parse", "HEAD")
