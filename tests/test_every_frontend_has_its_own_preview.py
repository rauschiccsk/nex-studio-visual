"""DEV-162 — in the Vizuál every frontend of a project has its own live preview, link, checklist and commit.

The Vizuál built one preview from ``/opt/projects/<slug>/frontend`` under ``vizual-<slug>``: a Web project's public
web (``web/``) had no preview, its screens were not committed with the approved Vizuál, the release check of the
preview switch never read it and deleting the project left nothing to remove for it. Director 20.10.2026: „Ideme
kokpit naučiť stavať projekt s dvoma frontendmi.“ A Štandardný project's preview stays exactly as it was.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from backend.db.models.projects import Project
from backend.services import orchestrator, preview_switch, vizual_sandbox
from backend.services.pipeline_status import VizualCheckItem, VizualChecklist
from tests.test_orchestrator_v2_vizual import _make_version, _seed_state
from tests.test_project_router import _payload, creator, router_client, uat_root  # noqa: F401 — fixtures

# --- names and mounts ------------------------------------------------------------------------------------------------


def test_the_main_preview_keeps_its_name_and_the_web_gets_its_own():
    assert vizual_sandbox.container_name("kariera") == "vizual-kariera"
    assert vizual_sandbox.public_url("kariera") == "https://vizual-kariera.isnex.eu"
    assert vizual_sandbox.container_name("kariera", "web") == "vizualweb-kariera"
    assert (
        vizual_sandbox.public_url("kariera", private=True, directory="web") == "https://vizualweb-kariera.int.isnex.eu"
    )


@pytest.mark.parametrize("slug", ["kariera", "kariera-web", "web-kariera", "a-web-b"])
def test_no_projects_web_preview_can_carry_another_projects_name(slug):
    """A slug may end or start in ``web``: the web's name must never equal any project's main preview."""
    web = vizual_sandbox.container_name(slug, "web")
    assert not web.startswith("vizual-"), (
        f"{web} could be the main preview of the project {web.removeprefix('vizual-')}"
    )


@pytest.mark.parametrize("directory", ["../x", "web/", "Web", "", "web-x"])
def test_a_directory_that_is_not_one_plain_word_is_refused(directory):
    with pytest.raises(ValueError):
        vizual_sandbox.container_name("kariera", directory)


def test_the_web_preview_mounts_web_and_answers_on_its_own_host(tmp_path):
    web = tmp_path / "web"
    web.mkdir()

    argv = vizual_sandbox.build_run_argv(slug="kariera", frontend_host_path=web, directory="web")

    assert argv[argv.index("--name") + 1] == "vizualweb-kariera"
    assert f"{web}:/app" in argv
    assert "traefik.http.routers.vizualweb-kariera.rule=Host(`vizualweb-kariera.isnex.eu`)" in argv
    assert "VIZUAL_PUBLIC_HOST=vizualweb-kariera.isnex.eu" in argv


def test_the_webs_preview_config_stays_out_of_git(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / "web").mkdir()

    vizual_sandbox._write_override_config(tmp_path / "web")

    assert "web/vite.vizual.config.mts" in (tmp_path / ".git" / "info" / "exclude").read_text().split()


# --- the release check of the preview switch ------------------------------------------------------------------------


def test_the_preview_switch_check_reads_every_frontend(tmp_path):
    for directory in ("frontend", "web"):
        (tmp_path / directory / "src").mkdir(parents=True)
    (tmp_path / "web" / "src" / "api.ts").write_text("if (import.meta.env.VITE_PREVIEW) { start(); }\n")

    assert preview_switch.find(tmp_path) == []  # a Štandardný project reads frontend/ only, as before
    found = preview_switch.find(tmp_path, ("frontend", "web"))
    assert [(f.path, f.line) for f in found] == [("web/src/api.ts", 1)]


# --- the approved Vizuál is committed with every frontend -----------------------------------------------------------


def test_the_approved_vizual_commits_the_webs_screens_too(tmp_path):
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, text=True, check=True).stdout

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    for directory in ("frontend", "web", "docs"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "a.txt").write_text(directory)

    with pytest.MonkeyPatch.context() as mp:
        for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}.items():
            mp.setenv(key, value)
        mp.setenv("GIT_COMMITTER_EMAIL", "t@t")
        orchestrator._commit_vizual_changes(tmp_path, ("frontend", "web"))

    assert sorted(git("show", "--name-only", "--format=", "HEAD").split()) == [
        "docs/a.txt",
        "frontend/a.txt",
        "web/a.txt",
    ]


# --- the checklist links each screen to its frontend's preview ------------------------------------------------------


def test_each_checked_screen_links_to_the_preview_of_its_frontend(db_session):
    version, project = _web_version(db_session)
    surfaces = orchestrator._surfaces_for_version(db_session, version.id)
    urls = {"frontend": "https://vizual-k.isnex.eu", "web": "https://vizualweb-k.isnex.eu"}
    checklist = VizualChecklist(
        items=[
            VizualCheckItem(screen="Registrácia", path="/register", action="Vyplň.", expected="Účet.", frontend="web"),
            VizualCheckItem(screen="Prehľad", path="/", action="Pozri.", expected="Karty."),
        ]
    )

    shown = orchestrator._vizual_checklist_payload(checklist, urls, "first", surfaces)

    assert [(i["screen"], i["url"], i["frontend"]) for i in shown["items"]] == [
        ("Registrácia", "https://vizualweb-k.isnex.eu/register", "Web"),
        ("Prehľad", "https://vizual-k.isnex.eu/", "Administrácia"),
    ]


def test_a_standard_projects_checklist_is_as_it_was(db_session):
    version, _project = _make_version(db_session)
    checklist = VizualChecklist(items=[VizualCheckItem(screen="Prehľad", path="/", action="Pozri.", expected="Karty.")])

    shown = orchestrator._vizual_checklist_payload(checklist, {"frontend": "https://vizual-p.isnex.eu"}, "first")

    assert shown["items"] == [
        {"screen": "Prehľad", "action": "Pozri.", "expected": "Karty.", "url": "https://vizual-p.isnex.eu/"}
    ]


def test_the_brief_asks_which_frontend_a_checked_screen_is_in_only_in_a_web_project(db_session):
    web, _ = _web_version(db_session)
    standard, _ = _make_version(db_session)

    assert "`frontend` (priečinok frontendu, v ktorom obrazovka je: `frontend` alebo `web`)" in (
        orchestrator._vizual_directive(db_session, web.id, None)
    )
    assert "priečinok frontendu, v ktorom" not in orchestrator._vizual_directive(db_session, standard.id, None)


# --- the preview's state and its restart, per frontend --------------------------------------------------------------


def _web_version(db_session):
    version, project = _make_version(db_session)
    project.type, project.backend_port, project.frontend_port, project.db_port, project.web_port = (
        "web",
        14950,
        14951,
        14952,
        14955,
    )
    db_session.flush()
    return version, project


def _previews(monkeypatch, states: dict[str, str]) -> list[str]:
    """Fake previews: *states* by directory; returns the directories spin_up was asked to start."""
    started: list[str] = []

    def _state(slug, *, private=False, directory="frontend"):
        state = states[directory]
        return {
            "state": state,
            "url": vizual_sandbox.public_url(slug, directory=directory) if state == "running" else None,
            "reason": "Error: port 5173 is in use" if state == "stopped" else None,
        }

    def _spin(slug, frontend_path=None, *, private=False, directory="frontend"):
        started.append(directory)
        states[directory] = "running"
        return vizual_sandbox.public_url(slug, directory=directory)

    monkeypatch.setattr(vizual_sandbox, "preview_state", _state)
    monkeypatch.setattr(vizual_sandbox, "spin_up", _spin)
    monkeypatch.setattr(orchestrator, "has_vizual_mockup", lambda db, vid: False)
    return started


@pytest.mark.asyncio
async def test_a_web_project_sees_each_preview_and_which_one_is_down(db_session, monkeypatch):
    version, project = _web_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _previews(monkeypatch, {"frontend": "running", "web": "stopped"})

    preview = await orchestrator.vizual_preview(db_session, version.id)

    assert (preview["state"], preview["reason"]) == ("stopped", "Web: Error: port 5173 is in use")
    assert [(f["label"], f["state"], f["url"]) for f in preview["frontends"]] == [
        ("Administrácia", "running", f"https://vizual-{project.slug}.isnex.eu"),
        ("Web", "stopped", None),
    ]


@pytest.mark.asyncio
async def test_start_again_starts_only_the_preview_that_is_down(db_session, monkeypatch):
    version, project = _web_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    started = _previews(monkeypatch, {"frontend": "running", "web": "stopped"})

    preview = await orchestrator.restart_vizual_preview(db_session, version.id)

    assert started == ["web"]
    assert preview["state"] == "running"
    assert [f["url"] for f in preview["frontends"]] == [
        f"https://vizual-{project.slug}.isnex.eu",
        f"https://vizualweb-{project.slug}.isnex.eu",
    ]


@pytest.mark.asyncio
async def test_a_standard_projects_preview_reads_as_before(db_session, monkeypatch):
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _previews(monkeypatch, {"frontend": "running"})

    preview = await orchestrator.vizual_preview(db_session, version.id)

    assert preview == {
        "needed": True,
        "state": "running",
        "url": f"https://vizual-{project.slug}.isnex.eu",
        "reason": None,
    }


@pytest.mark.asyncio
async def test_the_vizual_round_starts_the_preview_of_both_frontends_and_names_both_links(db_session, monkeypatch):
    from tests.test_orchestrator_v2_vizual import _msgs, _seed_drawn

    version, project = _web_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _seed_drawn(db_session, version.id)
    started = _previews(monkeypatch, {"frontend": "missing", "web": "missing"})

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_manazer"
    assert started == ["frontend", "web"]
    (note,) = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    admin, web = f"https://vizual-{project.slug}.isnex.eu", f"https://vizualweb-{project.slug}.isnex.eu"
    assert note.payload["vizual_url"] == admin
    assert note.payload["vizual_urls"] == [{"label": "Administrácia", "url": admin}, {"label": "Web", "url": web}]
    assert note.content == f"Vizuál je pripravený — otvor si ho: Administrácia {admin}, Web {web}"


# --- deleting the project removes every preview ---------------------------------------------------------------------


@pytest.mark.usefixtures("_isolate_create_project_kb")
def test_deleting_a_web_project_removes_the_preview_of_both_frontends(router_client, creator, db_session, monkeypatch):  # noqa: F811
    removed: list[str] = []
    monkeypatch.setattr(vizual_sandbox, "_force_remove", removed.append)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"running": False})
    created = router_client.post("/api/v1/projects", json=_payload(creator.id))
    assert created.status_code == 201, created.text
    project = created.json()
    row = db_session.get(Project, uuid.UUID(project["id"]))
    row.type, row.backend_port, row.frontend_port, row.db_port, row.web_port = "web", 14940, 14941, 14942, 14945
    db_session.flush()

    assert router_client.delete(f"/api/v1/projects/{project['id']}").status_code == 204

    assert removed == [f"vizual-{project['slug']}", f"vizualweb-{project['slug']}"]
