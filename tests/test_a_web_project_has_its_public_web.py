"""DEV-161 — a Web project has its public web on disk: founded with ``web/``, switched to it, checked like frontend/.

DEV-160 taught the cockpit that a Web project has two frontends — the administration in ``frontend/`` (sign-in by
username) and the public web in ``web/`` (sign-in by e-mail) — but the project's workspace still had one: the template
built only ``frontend/``, CI and the pre-commit check walked only ``frontend/``, nex-shared was pinned and upgraded only
there, and switching a project to Web changed nothing but a row. Director 20.10.2026: „Ideme kokpit naučiť stavať
projekt s dvoma frontendmi.“ 21.10.2026: the public registers itself by e-mail and password (Director: „Áno platia,
súhlasím“).
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.main import app
from backend.services import create_project_postscaffold as postscaffold
from backend.services import nexshared, port_registry, template_bootstrap
from backend.services import project_surfaces as ps
from backend.services import system_setting as system_setting_service

ICC_TEMPLATE = Path("/home/icc/knowledge/templates/claude-project")
ICC_INIT_SCRIPT = ICC_TEMPLATE / "init.sh"
BASE = 14930


@pytest.fixture(autouse=True)
def _no_registry_file(monkeypatch, tmp_path):
    """The KB port registry of this machine stays out of the tests — its blocks would answer for our ports."""
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", tmp_path / "port-registry.yaml")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(key, value)


def _user(db) -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    return user


def _project(db, owner, *, project_type="standard", source_path=None, base=BASE) -> Project:
    project = Project(
        name="Kariéra Web",
        slug=f"kariera-{uuid.uuid4().hex[:6]}",
        type=project_type,
        auth_mode="password",
        description="",
        created_by=owner.id,
        backend_port=base,
        frontend_port=base + 1,
        db_port=base + 2,
        web_port=base + 5 if project_type == "web" else None,
        source_path=str(source_path) if source_path else None,
        repo_url="rauschiccsk/kariera-web",
    )
    db.add(project)
    db.flush()
    return project


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- founding: init.sh gets --web exactly for a Web project ---------------------------------------------------------


@pytest.mark.parametrize(("project_type", "web_flag"), [("web", True), ("standard", False)])
def test_the_template_is_asked_for_the_web_exactly_for_a_web_project(db_session, tmp_path, project_type, web_flag):
    owner = _user(db_session)
    script = _script(tmp_path / "init.sh", "exit 0\n")
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    project = _project(db_session, owner, project_type=project_type, source_path=tmp_path / "ws")

    with patch.object(template_bootstrap.subprocess, "run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        template_bootstrap.invoke_init_script(db_session, project)

    argv = run.call_args.args[0]
    assert ("--web" in argv) is web_flag
    assert "--add-web" not in argv


def _real_template():
    if not ICC_INIT_SCRIPT.is_file():
        pytest.skip(f"the real template is not on this host ({ICC_TEMPLATE}) — cockpit/template drift unchecked here")


def test_a_web_project_is_founded_with_its_public_web(db_session, tmp_path):
    """The real template, for real: frontend/ is the administration (username), web/ the public web (e-mail)."""
    _real_template()
    system_setting_service.upsert(db_session, "template_init_script_path", str(ICC_INIT_SCRIPT))
    owner = _user(db_session)
    target = tmp_path / "web-probe"
    project = _project(db_session, owner, project_type="web", source_path=target)

    template_bootstrap.invoke_init_script(db_session, project)

    login_admin = (target / "frontend" / "src" / "pages" / "LoginPage.tsx").read_text(encoding="utf-8")
    login_web = (target / "web" / "src" / "pages" / "LoginPage.tsx").read_text(encoding="utf-8")
    assert 'fieldLabel="username"' in login_admin
    assert 'fieldLabel="email"' in login_web
    assert (target / "web" / "src" / "pages" / "RegisterPage.tsx").is_file()
    # The administration's own files stay out of the public web; nothing is left as a template.
    assert not (target / "web" / "src" / "components" / "layout" / "Sidebar.tsx").exists()
    assert not list((target / "web").rglob("*.tmpl"))
    assert json.loads((target / "web" / "package.json").read_text(encoding="utf-8"))["name"] == "web"
    tracked = _git(target, "ls-files", "web").splitlines()
    assert "web/src/pages/RegisterPage.tsx" in tracked


def test_a_standard_project_is_founded_without_a_web(db_session, tmp_path):
    _real_template()
    system_setting_service.upsert(db_session, "template_init_script_path", str(ICC_INIT_SCRIPT))
    owner = _user(db_session)
    target = tmp_path / "std-probe"
    project = _project(db_session, owner, project_type="standard", source_path=target)

    template_bootstrap.invoke_init_script(db_session, project)

    assert (target / "frontend" / "package.json").is_file()
    assert not (target / "web").exists()


def test_an_existing_project_gets_the_same_web_and_never_a_second_one(db_session, tmp_path):
    _real_template()
    system_setting_service.upsert(db_session, "template_init_script_path", str(ICC_INIT_SCRIPT))
    owner = _user(db_session)
    target = tmp_path / "switch-probe"
    project = _project(db_session, owner, project_type="standard", source_path=target)
    template_bootstrap.invoke_init_script(db_session, project)

    template_bootstrap.add_web_skeleton(db_session, project)

    assert 'fieldLabel="email"' in (target / "web" / "src" / "pages" / "LoginPage.tsx").read_text(encoding="utf-8")
    assert _git(target, "status", "--porcelain", "--", "web").startswith("?? web/"), "the caller commits, not init.sh"
    with pytest.raises(template_bootstrap.TemplateBootstrapError, match="Web sa do projektu nepodarilo pridať"):
        template_bootstrap.add_web_skeleton(db_session, project)


# --- founding: the web's CI ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("project_type", "wired"), [("web", True), ("standard", False)])
def test_the_founding_wires_the_webs_ci_exactly_for_a_web_project(tmp_path, project_type, wired):
    with (
        patch.object(postscaffold, "_run_smoke_test"),
        patch.object(postscaffold, "_seed_release_smoke_test"),
        patch.object(postscaffold, "_commit_and_push_scaffold_finalisation"),
        patch.object(postscaffold, "start_ci_runner", return_value=None),
        patch.object(postscaffold, "_wire_cicd_workflow"),
        patch.object(postscaffold, "_wire_web_workflow", return_value=None) as web_ci,
        patch.object(postscaffold, "_wire_precommit_hook"),
    ):
        postscaffold.run_post_scaffold_steps(
            target=str(tmp_path),
            slug="kariera",
            repo_url="rauschiccsk/kariera",
            project_type=project_type,
            auth_mode="password",
            enable_cicd=True,
            full_smoke=False,
            enable_branch_protection=False,
        )

    assert web_ci.called is wired


def _frontend_commands(workflow: dict, directory: str) -> set[str]:
    """Every shell line of the workflow that works in *directory* — with the directory taken out of it."""
    lines = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                line = line.strip()
                if line.startswith(f"cd {directory}"):
                    lines.add(line.replace(f"cd {directory} && ", "").replace(f"cd {directory}", "").strip())
                elif line.startswith("npm "):
                    lines.add(line)
    return {line for line in lines if line}


def test_the_webs_ci_runs_every_check_ci_runs_on_the_administration(tmp_path):
    """The rule, not a list: whatever ci.yml runs in frontend/, web.yml runs in web/ — on the project's own runner."""
    assert postscaffold.write_web_workflow(tmp_path, "kariera")
    web = yaml.safe_load((tmp_path / postscaffold.WEB_WORKFLOW).read_text(encoding="utf-8"))
    ci = yaml.safe_load(postscaffold.CICD_TEMPLATE.read_text(encoding="utf-8").replace("{{PROJECT_SLUG}}", "kariera"))

    administration = _frontend_commands(ci, "frontend")
    assert {"npm run type-check", "npm run lint", "npm test -- --run"} <= administration
    assert administration <= _frontend_commands(web, "web")
    assert {job["runs-on"] for job in web["jobs"].values()} == {"andros-ubuntu-kariera"}
    assert "{{" not in json.dumps(web["jobs"]).replace("${{", "")


def test_the_webs_ci_is_never_written_over_one_the_project_has(tmp_path):
    workflow = tmp_path / postscaffold.WEB_WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: own\n", encoding="utf-8")

    assert not postscaffold.write_web_workflow(tmp_path, "kariera")
    assert workflow.read_text(encoding="utf-8") == "name: own\n"


# --- the pre-commit check walks every frontend -----------------------------------------------------------------------


def _hook_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with the real hook, three package.json directories and a fake npm/node that log where they ran."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    hooks = repo / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(postscaffold.PRECOMMIT_HOOK_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", ".githooks")
    for directory in ("frontend", "web", "tools"):
        (repo / directory).mkdir()
        (repo / directory / "package.json").write_text('{"scripts": {"lint": "x", "type-check": "x"}}')
    for directory in ("frontend", "web"):  # tools/ has no vite.config.ts — not a frontend
        (repo / directory / "vite.config.ts").write_text("export default {};\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "npm.log"
    _script(
        bin_dir / "npm",
        f'echo "$(basename "$PWD") $*" >> {log}\n'
        'if [[ -f "$PWD/FAIL" && "$*" == "run type-check" ]]; then exit 1; fi\n',
    )
    _script(bin_dir / "node", "exit 0\n")
    return repo, bin_dir


def _commit(repo: Path, bin_dir: Path, *paths: str) -> subprocess.CompletedProcess:
    for path in paths:
        (repo / path).write_text("change\n", encoding="utf-8")
    _git(repo, "add", *paths)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], capture_output=True, text=True, env=env)


def test_the_pre_commit_check_type_checks_and_lints_every_frontend_it_touches(tmp_path):
    repo, bin_dir = _hook_repo(tmp_path)

    done = _commit(repo, bin_dir, "frontend/a.ts", "web/b.ts", "tools/c.js")

    assert done.returncode == 0, done.stdout + done.stderr
    ran = (tmp_path / "npm.log").read_text(encoding="utf-8").splitlines()
    assert ran == ["frontend run type-check", "frontend run lint", "web run type-check", "web run lint"]


def test_a_red_web_stops_the_commit(tmp_path):
    repo, bin_dir = _hook_repo(tmp_path)
    (repo / "web" / "FAIL").write_text("")

    done = _commit(repo, bin_dir, "web/b.ts")

    assert done.returncode != 0
    assert "web type-check FAILED" in done.stdout + done.stderr
    no_commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True)
    assert no_commit.returncode != 0, "the red web was committed"


def test_a_frontend_that_is_not_touched_is_not_checked(tmp_path):
    repo, bin_dir = _hook_repo(tmp_path)
    (repo / "web" / "FAIL").write_text("")

    done = _commit(repo, bin_dir, "frontend/a.ts")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "web" not in (tmp_path / "npm.log").read_text(encoding="utf-8")


# --- nex-shared: every frontend, all or none ------------------------------------------------------------------------


def _lock(version: str) -> str:
    return json.dumps({"lockfileVersion": 3, "packages": {"node_modules/nex-shared": {"version": version}}})


def _seed(root: Path, directory: str, version: str) -> None:
    fe = root / directory
    fe.mkdir(parents=True, exist_ok=True)
    (fe / "package.json").write_text(
        json.dumps({"dependencies": {"nex-shared": f"github:rauschiccsk/nex-shared#v{version}"}}), encoding="utf-8"
    )
    (fe / "package-lock.json").write_text(_lock(version), encoding="utf-8")


def test_the_status_names_each_frontend_and_the_oldest_decides(tmp_path):
    _seed(tmp_path, "frontend", "0.19.0")
    _seed(tmp_path, "web", "0.15.0")

    status = nexshared.status_for_source(
        str(tmp_path), directories=("frontend", "web"), tags=["0.15.0", "0.19.0", "0.23.0"], changelog_text=""
    )

    assert status["frontends"] == [
        {"directory": "frontend", "current": "0.19.0"},
        {"directory": "web", "current": "0.15.0"},
    ]
    assert (status["current"], status["behind"]) == ("0.15.0", 2)


def test_a_frontend_without_a_lockfile_offers_nothing(tmp_path):
    _seed(tmp_path, "frontend", "0.19.0")
    (tmp_path / "web").mkdir()

    status = nexshared.status_for_source(str(tmp_path), directories=("frontend", "web"), tags=["0.19.0", "0.23.0"])

    assert status["current"] is None and not status["up_to_date"]


def test_an_upgrade_moves_every_frontend(tmp_path, monkeypatch):
    _seed(tmp_path, "frontend", "0.19.0")
    _seed(tmp_path, "web", "0.19.0")

    def resolve(frontend_dir: Path, spec: str, **_kwargs) -> bool:
        (frontend_dir / "package-lock.json").write_text(_lock(spec.rsplit("#v", 1)[1]), encoding="utf-8")
        return True

    monkeypatch.setattr(nexshared, "resolve_lockfile", resolve)

    assert nexshared.upgrade_source_pin(str(tmp_path), "0.23.0", directories=("frontend", "web"))
    assert [nexshared.effective_version(str(tmp_path), d) for d in ("frontend", "web")] == ["0.23.0", "0.23.0"]


def test_an_upgrade_the_web_cannot_follow_leaves_both_frontends_as_they_were(tmp_path, monkeypatch):
    _seed(tmp_path, "frontend", "0.19.0")
    _seed(tmp_path, "web", "0.19.0")
    before = {p: p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json")}

    def resolve(frontend_dir: Path, spec: str, **_kwargs) -> bool:
        if frontend_dir.name == "web":
            return False
        (frontend_dir / "package-lock.json").write_text(_lock(spec.rsplit("#v", 1)[1]), encoding="utf-8")
        return True

    monkeypatch.setattr(nexshared, "resolve_lockfile", resolve)

    assert not nexshared.upgrade_source_pin(str(tmp_path), "0.23.0", directories=("frontend", "web"))
    assert {p: p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json")} == before


def test_the_upgrade_commits_every_frontend(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _seed(tmp_path, "frontend", "0.23.0")
    _seed(tmp_path, "web", "0.23.0")

    assert nexshared.commit_pin_upgrade(str(tmp_path), "0.23.0", directories=("frontend", "web"))
    assert sorted(_git(tmp_path, "show", "--name-only", "--format=", "HEAD").split()) == [
        "frontend/package-lock.json",
        "frontend/package.json",
        "web/package-lock.json",
        "web/package.json",
    ]


def test_the_status_route_names_the_frontends_the_way_the_manazer_knows_them(client, db_session, tmp_path):
    owner = _user(db_session)
    project = _project(db_session, owner, project_type="web", source_path=tmp_path)
    _seed(tmp_path, "frontend", "0.19.0")
    _seed(tmp_path, "web", "0.19.0")
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)

    with (
        patch.object(nexshared, "list_remote_tags", return_value=["0.19.0"]),
        patch.object(nexshared, "fetch_changelog", return_value=""),
    ):
        status = client.get(f"/api/v1/projects/{project.id}/nexshared-status").json()

    assert [(f["label"], f["directory"], f["current"]) for f in status["frontends"]] == [
        ("Administrácia", "frontend", "0.19.0"),
        ("Web", "web", "0.19.0"),
    ]


# --- the switch to Web gives the workspace its web ------------------------------------------------------------------

FAKE_ADD_WEB = """
target=""; add=0
while [[ $# -gt 0 ]]; do
  case "$1" in --target) target="$2"; shift 2 ;; --add-web) add=1; shift ;; *) shift ;; esac
done
[[ "$add" == 1 ]] || { echo "ERROR: expected --add-web" >&2; exit 2; }
[[ ! -e "$target/web" ]] || { echo "ERROR: --add-web: $target/web already exists" >&2; exit 1; }
mkdir -p "$target/web/src"
echo '{"name": "web"}' > "$target/web/package.json"
echo 'export default {};' > "$target/web/vite.config.ts"
"""

FAILING_ADD_WEB = """
target=""
while [[ $# -gt 0 ]]; do case "$1" in --target) target="$2"; shift 2 ;; *) shift ;; esac; done
mkdir -p "$target/web/src" && echo half > "$target/web/src/half.ts"
echo "ERROR: web-skeleton/.skeleton-remove names 'x', which frontend-skeleton/ does not have" >&2
exit 1
"""


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A founded project's workspace with CI and the OLD pre-commit check, pushed to its own origin."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    (ws / "CLAUDE.md").write_text("# project\n")
    (ws / ".github" / "workflows").mkdir(parents=True)
    (ws / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
    (ws / ".githooks").mkdir()
    (ws / ".githooks" / "pre-commit").write_text("#!/usr/bin/env bash\n# frontend/ only\nexit 0\n")
    (ws / "frontend").mkdir()
    (ws / "frontend" / "package.json").write_text("{}")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    _git(ws, "remote", "add", "origin", str(origin))
    _git(ws, "push", "-q", "origin", "main")
    return ws, origin


@pytest.fixture
def web_open(monkeypatch):
    monkeypatch.setattr(ps, "WEB_PROJECTS_AVAILABLE", True)


def test_the_switch_gives_the_workspace_its_web_its_ci_and_the_new_check_in_one_pushed_commit(
    client, db_session, tmp_path, web_open, monkeypatch
):
    ws, origin = _workspace(tmp_path)
    script = _script(tmp_path / "init.sh", FAKE_ADD_WEB)
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    owner = _user(db_session)
    project = _project(db_session, owner, source_path=ws)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)
    monkeypatch.setattr("backend.api.routes.project_surfaces.chown_workspace", lambda _path: None)

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert answer.status_code == 200, answer.text
    assert answer.json()["warnings"] == []
    db_session.refresh(project)
    assert project.type == "web"
    changed = sorted(_git(ws, "show", "--name-only", "--format=", "HEAD").split())
    assert changed == [
        ".githooks/pre-commit",
        ".github/workflows/web.yml",
        "web/package.json",
        "web/vite.config.ts",
    ]
    assert (ws / ".githooks" / "pre-commit").read_text() == postscaffold.PRECOMMIT_HOOK_TEMPLATE.read_text()
    assert "runs-on: andros-ubuntu-" + project.slug in (ws / ".github" / "workflows" / "web.yml").read_text()
    assert _git(origin, "rev-parse", "main") == _git(ws, "rev-parse", "HEAD"), "the switch was not pushed"
    assert _git(ws, "status", "--porcelain") == ""


def test_a_web_the_template_cannot_build_leaves_the_project_as_it_was(client, db_session, tmp_path, web_open):
    ws, _origin = _workspace(tmp_path)
    head = _git(ws, "rev-parse", "HEAD")
    script = _script(tmp_path / "init.sh", FAILING_ADD_WEB)
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    owner = _user(db_session)
    project = _project(db_session, owner, source_path=ws)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert answer.status_code == 409
    assert re.match(r"Web sa do projektu nepodarilo pridať: ERROR: web-skeleton", answer.json()["detail"])
    db_session.refresh(project)
    assert (project.type, project.web_port) == ("standard", None)
    assert not (ws / "web").exists(), "half a web stayed behind"
    assert _git(ws, "rev-parse", "HEAD") == head


def test_a_web_that_was_already_there_is_never_removed(client, db_session, tmp_path, web_open):
    ws, _origin = _workspace(tmp_path)
    (ws / "web").mkdir()
    (ws / "web" / "mine.ts").write_text("// someone's work\n")
    script = _script(tmp_path / "init.sh", FAKE_ADD_WEB)
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    owner = _user(db_session)
    project = _project(db_session, owner, source_path=ws)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert answer.status_code == 409
    assert answer.json()["detail"].startswith("V priečinku projektu sú neuložené zmeny (1)")
    assert (ws / "web" / "mine.ts").read_text() == "// someone's work\n"


def _switch(client, db_session, tmp_path, ws: Path, script_body: str = FAKE_ADD_WEB):
    script = _script(tmp_path / "init.sh", script_body)
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    owner = _user(db_session)
    project = _project(db_session, owner, source_path=ws)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)
    return project, client.post(f"/api/v1/projects/{project.id}/web-switch")


def test_unsaved_work_in_the_workspace_stops_the_switch_and_is_never_committed(client, db_session, tmp_path, web_open):
    ws, _origin = _workspace(tmp_path)
    (ws / "frontend" / "half-done.ts").write_text("// unsaved\n")
    _git(ws, "add", "frontend/half-done.ts")
    head = _git(ws, "rev-parse", "HEAD")

    project, answer = _switch(client, db_session, tmp_path, ws)

    assert answer.status_code == 409
    assert "neuložené zmeny (1)" in answer.json()["detail"]
    db_session.refresh(project)
    assert project.type == "standard"
    assert _git(ws, "rev-parse", "HEAD") == head and not (ws / "web").exists()


def test_the_switch_commits_only_its_own_files(tmp_path):
    ws, _origin = _workspace(tmp_path)
    (ws / "frontend" / "staged-by-someone.ts").write_text("// not ours\n")
    _git(ws, "add", "frontend/staged-by-someone.ts")
    (ws / "web").mkdir()
    (ws / "web" / "package.json").write_text("{}")

    assert postscaffold.commit_and_push(ws, ["web"], "feat(web): x", "kariera", scaffold=True) is None
    assert _git(ws, "show", "--name-only", "--format=", "HEAD").split() == ["web/package.json"]
    assert "A  frontend/staged-by-someone.ts" in _git(ws, "status", "--porcelain")


def test_the_switch_pushes_the_branch_the_project_is_on(tmp_path):
    ws, origin = _workspace(tmp_path)
    _git(ws, "branch", "-m", "main", "master")
    (ws / "web").mkdir()
    (ws / "web" / "package.json").write_text("{}")

    assert postscaffold.commit_and_push(ws, ["web"], "feat(web): x", "kariera", scaffold=True) is None
    assert _git(origin, "rev-parse", "master") == _git(ws, "rev-parse", "HEAD")


def test_the_template_commit_still_stamps_the_version(tmp_path):
    """--no-verify skips only the pre-commit check: the template's post-commit version stamp still runs."""
    ws, _origin = _workspace(tmp_path)
    _script(ws / ".githooks" / "post-commit", 'echo stamped > "$(git rev-parse --show-toplevel)/.stamp"\n')
    _git(ws, "config", "core.hooksPath", ".githooks")
    (ws / "web").mkdir()
    (ws / "web" / "package.json").write_text("{}")

    postscaffold.commit_and_push(ws, ["web"], "feat(web): x", "kariera", scaffold=True)

    assert (ws / ".stamp").read_text() == "stamped\n"


def test_a_switch_that_did_not_finish_is_finished_without_building_the_web_again(
    client, db_session, tmp_path, web_open, monkeypatch
):
    ws, origin = _workspace(tmp_path)
    (ws / "web").mkdir()
    (ws / "web" / "package.json").write_text('{"name": "web"}')
    _git(ws, "add", "web")
    _git(ws, "commit", "-q", "-m", "feat(web): the public web (earlier switch)")
    monkeypatch.setattr("backend.api.routes.project_surfaces.chown_workspace", lambda _path: None)

    project, answer = _switch(client, db_session, tmp_path, ws, script_body='echo "ERROR: called again" >&2; exit 1\n')

    assert answer.status_code == 200, answer.text
    db_session.refresh(project)
    assert project.type == "web"
    assert sorted(_git(ws, "show", "--name-only", "--format=", "HEAD").split()) == [
        ".githooks/pre-commit",
        ".github/workflows/web.yml",
    ]
    assert _git(origin, "rev-parse", "main") == _git(ws, "rev-parse", "HEAD")


def test_a_project_an_agent_works_on_is_not_switched(client, db_session, tmp_path, web_open):
    from backend.db.models.pipeline import PipelineState
    from backend.db.models.versions import Version

    ws, _origin = _workspace(tmp_path)
    owner = _user(db_session)
    project = _project(db_session, owner, source_path=ws)
    version = Version(project_id=project.id, version_number="1.0.0")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            status="agent_working",
            current_actor="ai_agent",
        )
    )
    db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert answer.status_code == 409
    assert (
        answer.json()["detail"] == "Na projekte práve pracuje agent — prepnúť ho na Web sa dá, až keď stavba zastaví."
    )
    assert not (ws / "web").exists()
