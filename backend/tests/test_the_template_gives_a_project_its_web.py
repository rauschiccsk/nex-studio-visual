"""DEV-523 — a project on the app template gets its web from the template: founded as a Web project, or with „Pridať
webový frontend“ later. The kit release's own ``render.py --add-web`` writes ``web/``; the project's frontend and server
move to that release (the web signs in on its server's web accounts); a step that fails puts everything back.
26.11.2026 orthodox-register: the Director — „Spravme to tak, aby kopit aj do budúcna mal takú funkciu - pridať webový
frontend.“"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.services import app_template
from backend.services.app_template import AppTemplateError

TAGS = "aaa\trefs/tags/v0.22.0\nbbb\trefs/tags/v0.23.0"
OLD_SERVER = (
    "nex-app-kit @ https://github.com/rauschiccsk/nex-app-kit/archive/refs/tags/v0.22.0.tar.gz#subdirectory=server"
)
PYPROJECT = f'[project]\nname = "app"\ndependencies = [\n    "{OLD_SERVER}",\n    "fastapi>=0.110",\n]\n'
SCAFFOLD_PACKAGE = {
    "dependencies": {
        "nex-app-kit": "github:rauschiccsk/nex-app-kit#v0.23.0",
        "nex-shared": "github:rauschiccsk/nex-shared#v0.43.0",
    }
}


class FakeWorld:
    """git, the renderer and npm — the renderer does what ``render.py --add-web`` does to the project."""

    def __init__(self, project: Path, fail: str | None = None) -> None:
        self.project = project
        self.fail = fail
        self.calls: list[list[str]] = []
        self.values: dict | None = None

    def __call__(self, argv: list[str], cwd: str | None = None, **_kwargs) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if self.fail and self.fail in " ".join(argv):
            return subprocess.CompletedProcess(argv, 1, "", "npm ERR! nedá sa")
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(argv, 0, TAGS, "")
        if argv[0] == "python3":
            self.values = json.loads(Path(argv[argv.index("--values") + 1]).read_text("utf-8"))
            web = self.project / "web"
            web.mkdir()
            (web / "package.json").write_text(json.dumps(SCAFFOLD_PACKAGE), "utf-8")
            (web / "app.manifest.json").write_text('{"name": "z kostry"}', "utf-8")
            manifest = self.project / app_template.MANIFEST
            manifest.write_text(json.dumps({**json.loads(manifest.read_text("utf-8")), "web": True}), "utf-8")
            compose = self.project / "docker-compose.yml"
            compose.write_text(compose.read_text("utf-8") + "  web:\n", "utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")


def kit(_version, destination: Path, _run) -> None:
    destination.mkdir(parents=True)
    (destination / "package.json").write_text(json.dumps({"peerDependencies": {}}), "utf-8")


def project_on_the_template(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()
    manifest = {
        "template": "nsv-standard",
        "name": "Orthodox Register",
        "initials": "OR",
        "auth": "password",
        "nav": [],
    }
    (tmp_path / app_template.MANIFEST).write_text(json.dumps(manifest), "utf-8")
    package = {
        "dependencies": {
            "nex-app-kit": "github:rauschiccsk/nex-app-kit#v0.22.0",
            "nex-shared": "github:rauschiccsk/nex-shared#v0.42.1",
            "react": "^19",
        }
    }
    (tmp_path / "frontend" / "package.json").write_text(json.dumps(package), "utf-8")
    (tmp_path / "frontend" / "package-lock.json").write_text("{}", "utf-8")
    (tmp_path / "backend" / "pyproject.toml").write_text(PYPROJECT, "utf-8")
    (tmp_path / "docker-compose.yml").write_text("services:\n  backend:\n", "utf-8")
    return tmp_path


def add(project: Path, world: FakeWorld) -> str:
    return app_template.add_web_files(
        project,
        template="nsv-standard",
        name="Orthodox Register",
        slug="orthodox-register",
        auth_mode="password",
        ports=(10240, 10241, 10242),
        web_port=10245,
        run=world,
        fetch_kit=kit,
    )


def test_the_web_comes_from_the_newest_release_and_the_project_moves_to_it(tmp_path):
    project = project_on_the_template(tmp_path)
    world = FakeWorld(project)

    version = add(project, world)

    assert version == "0.23.0"
    render = next(argv for argv in world.calls if argv[0] == "python3")
    assert render[-3:] == ["--into", str(project), "--add-web"]
    assert (world.values["web"], world.values["web_port"], world.values["ai"]) == (True, 10245, False)
    package = json.loads((project / "frontend" / "package.json").read_text("utf-8"))["dependencies"]
    assert package["nex-app-kit"].endswith("#v0.23.0") and package["nex-shared"].endswith("#v0.43.0")
    assert package["react"] == "^19"  # the project's own packages stay
    pyproject = (project / "backend" / "pyproject.toml").read_text("utf-8")
    assert (
        pyproject.count("nex-app-kit @") == 1 and "tags/v0.23.0.tar.gz" in pyproject and "fastapi>=0.110" in pyproject
    )
    locked = [argv for argv in world.calls if argv[:2] == ["npm", "install"]]
    assert len(locked) == 2  # the frontend and the web


def test_the_web_s_manifest_names_the_app_as_the_administration_s_does(tmp_path):
    project = project_on_the_template(tmp_path)

    add(project, FakeWorld(project))

    web = json.loads((project / "web" / "app.manifest.json").read_text("utf-8"))
    assert web == {
        "template": "nsv-standard",
        "name": "Orthodox Register",
        "initials": "OR",
        "auth": "password",
        "web": True,
    }


def test_a_step_that_fails_puts_everything_back(tmp_path):
    project = project_on_the_template(tmp_path)
    before = {p: p.read_text("utf-8") for p in project.rglob("*") if p.is_file()}

    with pytest.raises(AppTemplateError) as refused:
        add(project, FakeWorld(project, fail="npm install"))

    assert str(refused.value).startswith("Webový frontend: zoznam balíkov sa nepodarilo pripraviť (npm install):")
    assert not (project / "web").exists()
    assert {p: p.read_text("utf-8") for p in project.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    ("prepare", "sentence"),
    [
        (lambda p: (p / "web").mkdir(), "Projekt už má priečinok web/ — webový frontend sa nepridá druhý raz."),
        (lambda p: (p / app_template.MANIFEST).unlink(), "Projekt nemá manifest šablóny (frontend/app.manifest.json)"),
    ],
)
def test_a_project_that_cannot_get_it_is_told_why(tmp_path, prepare, sentence):
    project = project_on_the_template(tmp_path)
    prepare(project)

    with pytest.raises(AppTemplateError) as refused:
        add(project, FakeWorld(project))

    assert str(refused.value).startswith(sentence)


def test_founding_a_web_project_on_the_template_fills_and_locks_its_web(tmp_path):
    (tmp_path / ".git").mkdir()
    world = FakeWorld(tmp_path)
    world_render = world.__call__

    def founding(argv, cwd=None, **kwargs):
        if argv[0] == "python3":  # founding renders the whole app; only the values matter here
            world.calls.append(argv)
            world.values = json.loads(Path(argv[argv.index("--values") + 1]).read_text("utf-8"))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return world_render(argv, cwd, **kwargs)

    app_template.apply_template(
        tmp_path,
        app_template.TemplateChoice("nsv-standard", ai=False),
        name="Orthodox Register",
        slug="orthodox-register",
        auth_mode="password",
        ports=(10240, 10241, 10242),
        web_port=10245,
        run=founding,
        fetch_kit=kit,
    )

    assert (world.values["web"], world.values["web_port"]) == (True, 10245)
    locks = [argv for argv in world.calls if argv[:2] == ["npm", "install"]]
    assert len(locks) == 2
    added = next(argv for argv in world.calls if argv[:2] == ["git", "add"])
    assert "web" in added


def test_a_kit_server_line_is_replaced_never_doubled():
    replaced = app_template.with_kit_server(PYPROJECT, "nex-app-kit @ NEW")
    assert replaced.count("nex-app-kit @") == 1 and '"nex-app-kit @ NEW",' in replaced
    added = app_template.with_kit_server('[project]\ndependencies = [\n    "fastapi",\n]\n', "nex-app-kit @ NEW")
    assert added.count("nex-app-kit @ NEW") == 1


def test_adding_the_web_takes_the_template_s_web_for_a_project_on_it_and_commits_its_files(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from backend.services import template_bootstrap, web_workspace

    project_dir = project_on_the_template(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(web_workspace, "unsaved_work", lambda _p: None)
    monkeypatch.setattr(web_workspace, "_tracked", lambda _t, _path: False)
    monkeypatch.setattr(web_workspace, "_changed", lambda _t, _path: True)
    monkeypatch.setattr(web_workspace.postscaffold, "web_claude_md_section", lambda: "")
    monkeypatch.setattr(
        web_workspace.postscaffold,
        "commit_and_push",
        lambda _t, paths, *_a, **_k: seen.setdefault("paths", paths) and None,
    )
    (project_dir / "CLAUDE.md").write_text("# p\n", "utf-8")
    monkeypatch.setattr(app_template, "add_web_files", lambda target, **kwargs: seen.setdefault("template", kwargs))
    monkeypatch.setattr(template_bootstrap, "add_web_skeleton", lambda *_a: seen.setdefault("skeleton", True))
    on_template = SimpleNamespace(
        source_path=str(project_dir),
        template="nsv-standard",
        name="Orthodox Register",
        slug="orthodox-register",
        auth_mode="password",
        backend_port=10240,
        frontend_port=10241,
        db_port=10242,
        web_port=10245,
    )

    web_workspace.add_web(None, on_template)

    assert "skeleton" not in seen and seen["template"]["web_port"] == 10245
    assert set(app_template.WEB_TEMPLATE_FILES) <= set(seen["paths"]) and "web" in seen["paths"]


def test_a_project_without_the_template_keeps_the_old_web(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from backend.services import template_bootstrap, web_workspace

    (tmp_path / "CLAUDE.md").write_text("# p\n", "utf-8")
    seen: dict = {}
    monkeypatch.setattr(web_workspace, "unsaved_work", lambda _p: None)
    monkeypatch.setattr(web_workspace, "_tracked", lambda _t, _path: False)
    monkeypatch.setattr(web_workspace, "_changed", lambda _t, _path: True)
    monkeypatch.setattr(web_workspace.postscaffold, "web_claude_md_section", lambda: "")
    monkeypatch.setattr(
        web_workspace.postscaffold,
        "commit_and_push",
        lambda _t, paths, *_a, **_k: seen.setdefault("paths", paths) and None,
    )
    monkeypatch.setattr(app_template, "add_web_files", lambda target, **kwargs: seen.setdefault("template", kwargs))
    monkeypatch.setattr(template_bootstrap, "add_web_skeleton", lambda *_a: seen.setdefault("skeleton", True))

    web_workspace.add_web(None, SimpleNamespace(source_path=str(tmp_path), template=None, slug="cas"))

    assert seen.get("skeleton") is True and "template" not in seen
    assert not set(app_template.WEB_TEMPLATE_FILES) & set(seen["paths"])
