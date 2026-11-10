"""DEV-342 — deleting a project leaves nothing behind and takes nothing it must not: the idea goes back to Nápady.

10.11.2026: Nazar founded Liturgikon from his idea „Orthodox Typikon“ before he was meant to, and the Director asked to
delete it „tak akoby ten projekt nebol založený“. Measured: „Zmazať natrvalo“ would have wiped the idea's talk (founding
moves it to the project, a deleted project takes its conversations with it) and left the idea founded in nothing, kept
the project's port block in the registry and its images on the disk, and said nothing when GitHub refused to delete the
repository. Director: „Súhlasím založ tiket a sprav to, lebo nápady nesmieme mazať“.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.db.models.idea import OUTCOME_FOUNDED, Idea
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.services import github_validation, orchestrator, port_registry
from backend.services import project as project_service
from backend.services.orchestrator import throw_away_project_images as real_throw_away_project_images
from backend.services.poradca import ideas
from tests.test_deleting_a_project_removes_its_preview import _create
from tests.test_ideas_end_in_a_project import PAID_BRIEF, _client, _idea_with, _new_project, world  # noqa: F401
from tests.test_ideas_talk_without_a_project import answers  # noqa: F401 — fixture of world
from tests.test_project_router import _payload, creator, router_client, uat_root  # noqa: F401 — fixtures
from tests.test_the_cockpits_knowledge_base_writes_reach_github import (  # noqa: F401
    _allocate,
    _git,
    _github_head,
    _head,
    kb,
)

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


def _messages(db, conversation_id) -> int:
    return db.scalar(
        select(func.count()).select_from(PoradcaMessage).where(PoradcaMessage.conversation_id == conversation_id)
    )


# ── the idea goes back to Nápady, with its whole talk ────────────────────────────────────────────────────────────────


def _founded(world):  # noqa: F811
    db = world["db"]
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(db, world["me"])
    c = _client(db, world["me"])
    done = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    assert done.status_code == 200, done.text
    return db.get(Idea, idea["id"]), db.get(PoradcaConversation, idea["conversation_id"]), project


def test_a_founded_idea_goes_back_open_with_its_talk_and_survives_the_delete(world):  # noqa: F811
    db = world["db"]
    idea, talk, project = _founded(world)
    told = _messages(db, talk.id)
    assert talk.project_id == project.id and idea.plane_identifier, "founding moved the talk and filed the ticket"

    returned = ideas.back_to_ideas(db, project.id)
    project_service.delete(db, project.id)
    db.flush()
    db.expire_all()

    assert returned == [talk.title]
    idea, talk = db.get(Idea, idea.id), db.get(PoradcaConversation, talk.id)
    assert talk is not None and talk.project_id is None and talk.version_id is None
    assert _messages(db, talk.id) == told and told > 0
    assert (idea.outcome, idea.outcome_project_id, idea.outcome_message_id, idea.concluded_at) == (
        None,
        None,
        None,
        None,
    )
    assert (idea.plane_issue_id, idea.plane_identifier, idea.plane_url) == (None, None, None), (
        "a new founding files anew"
    )
    assert ideas.state(db, idea, talk) != OUTCOME_FOUNDED


def test_an_idea_taken_off_the_list_comes_back_too(world):  # noqa: F811
    """DEV-331 lets a founded idea leave the list while its talk stays with the project — with the project gone, the
    talk would have nowhere to be seen."""
    db = world["db"]
    idea, _talk, project = _founded(world)
    idea.deleted_at = datetime.now(timezone.utc)
    db.flush()

    ideas.back_to_ideas(db, project.id)

    assert db.get(Idea, idea.id).deleted_at is None


def test_an_idea_of_another_project_stays_where_it_ended(world):  # noqa: F811
    db = world["db"]
    idea, talk, project = _founded(world)
    other = Project(
        name="NEX Sklad",
        slug="nex-sklad",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=world["me"].id,
    )
    db.add(other)
    db.flush()

    assert ideas.back_to_ideas(db, other.id) == []

    assert (
        db.get(Idea, idea.id).outcome == OUTCOME_FOUNDED
        and db.get(PoradcaConversation, talk.id).project_id == project.id
    )


# ── the delete route says what it did ────────────────────────────────────────────────────────────────────────────


def _idea_of(db, owner, project_id: str, title: str = "Orthodox Typikon") -> PoradcaConversation:
    idea = Idea(author_id=owner.id, outcome=OUTCOME_FOUNDED, outcome_project_id=project_id, plane_identifier="DEV-1")
    db.add(idea)
    db.flush()
    talk = PoradcaConversation(
        author_id=owner.id, title=title, idea_id=idea.id, project_id=project_id, claude_session_id=str(uuid.uuid4())
    )
    db.add(talk)
    db.flush()
    db.add(PoradcaMessage(conversation_id=talk.id, author="human", content="Mám nápad.", status="done"))
    db.flush()
    return talk


def test_the_delete_gives_the_idea_back_and_says_so(router_client, creator, db_session):  # noqa: F811
    project = _create(router_client, creator)
    talk = _idea_of(db_session, creator, project["id"])

    response = router_client.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 200, response.text
    assert "Nápad „Orthodox Typikon“ je späť v Nápadoch aj s celým rozhovorom." in response.json()["notes"]
    db_session.expire_all()
    assert db_session.get(PoradcaConversation, talk.id) is not None, "the idea's talk was deleted with the project"
    assert _messages(db_session, talk.id) == 1


def test_the_delete_releases_the_ports_and_throws_the_images_away(router_client, creator, monkeypatch):  # noqa: F811
    released, thrown = [], []
    monkeypatch.setattr(port_registry, "release_allocation", lambda slug: released.append(slug) or "porty ostali")
    monkeypatch.setattr(orchestrator, "project_images", lambda compose: ["nex-foo-backend"])
    monkeypatch.setattr(
        orchestrator, "throw_away_project_images", lambda repos: thrown.append(repos) or "obrazy ostali"
    )
    project = _create(router_client, creator)

    said = router_client.delete(f"/api/v1/projects/{project['id']}").json()

    assert released == [project["slug"]] and thrown == [["nex-foo-backend"]]
    assert said["left_behind"] == ["porty ostali", "obrazy ostali"], "what stayed is not said as done"
    assert "porty ostali" not in said["notes"]


def _with_repository(client, owner) -> dict:
    created = client.post(
        "/api/v1/projects", json=_payload(owner.id, slug="liturgikon", repo_url="rauschiccsk/liturgikon")
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_project_without_a_repository_says_nothing_about_github(router_client, creator):  # noqa: F811
    project = _create(router_client, creator)
    assert not project["repo_url"]

    said = router_client.delete(f"/api/v1/projects/{project['id']}?delete_github=true").json()

    assert not [n for n in said["notes"] + said["left_behind"] if "Repozitár" in n]


@pytest.mark.parametrize(
    ("flag", "github", "said"),
    [
        (True, lambda repo, timeout: True, "je zmazaný."),
        (True, lambda repo, timeout: False, "už neexistoval."),
        (False, None, "ostal — jeho zmazanie nebolo zaškrtnuté."),
    ],
)
def test_the_delete_says_how_the_repository_ended(router_client, creator, monkeypatch, flag, github, said):  # noqa: F811
    called = []
    monkeypatch.setattr(
        github_validation, "delete_github_repo", lambda repo, timeout: called.append(repo) or github(repo, timeout)
    )
    project = _with_repository(router_client, creator)

    notes = router_client.delete(f"/api/v1/projects/{project['id']}?delete_github={str(flag).lower()}").json()["notes"]

    assert [n for n in notes if "Repozitár" in n] == [f"Repozitár {project['repo_url']} na GitHube {said}"]
    assert called == ([project["repo_url"]] if flag else [])


def test_a_repository_github_refused_to_delete_is_said_with_what_to_do(router_client, creator, monkeypatch):  # noqa: F811
    def refused(repo, timeout):
        raise RuntimeError("GitHub API refused repository deletion (403)")

    monkeypatch.setattr(github_validation, "delete_github_repo", refused)
    project = _with_repository(router_client, creator)

    response = router_client.delete(f"/api/v1/projects/{project['id']}?delete_github=true")

    assert response.status_code == 200, "a refused repository must not undo the delete"
    (note,) = [n for n in response.json()["left_behind"] if "Repozitár" in n]
    assert not [n for n in response.json()["notes"] if "Repozitár" in n], "a refused repository is not done"
    assert note.startswith(f"Repozitár {project['repo_url']} na GitHube ostal — GitHub zmazanie odmietol")
    assert note.endswith("Zmaž ho na GitHube ručne.")


# ── the port block leaves the registry ───────────────────────────────────────────────────────────────────────────


def test_a_deleted_projects_block_leaves_the_registry_and_github(kb):  # noqa: F811
    assert _allocate("liturgikon", 10280) is None and _allocate("orthodox-register", 10290) is None

    assert port_registry.release_allocation("liturgikon") is None

    committed = _git(kb, "show", "HEAD:infrastructure/port-registry.yaml")
    on_disk = (kb / "infrastructure/port-registry.yaml").read_text(encoding="utf-8")
    for text in (committed, on_disk):
        assert "vlastník: liturgikon" not in text and "rozsah: 10280-10289" not in text
        assert "vlastník: orthodox-register" in text, "another project's block stays"
    assert (
        _git(kb, "log", "-1", "--format=%s")
        == "chore(porty): blok projektu liturgikon uvoľnený — projekt zmazaný v kokpite\n"
    )
    assert _github_head(kb) == _head(kb)


def test_a_project_without_a_block_changes_nothing(kb):  # noqa: F811
    before = _head(kb)

    assert port_registry.release_allocation("nikto") is None

    assert _head(kb) == before


def test_a_released_block_that_cannot_reach_github_is_said(kb, tmp_path):  # noqa: F811
    assert _allocate("liturgikon", 10280) is None
    _git(kb, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))

    warning = port_registry.release_allocation("liturgikon")

    assert warning and warning.startswith(
        "Blok portov projektu liturgikon je uvoľnený na ANDROSe, ale na GitHub sa nedostal"
    )


# ── the images the cockpit built for it ──────────────────────────────────────────────────────────────────────────


def test_the_projects_images_are_the_ones_it_builds_by_their_exact_names(tmp_path):
    compose = tmp_path / "liturgikon" / "docker-compose.yml"
    compose.parent.mkdir()
    compose.write_text(
        "services:\n"
        "  backend: {build: ./backend}\n"
        "  frontend: {build: {context: ./frontend}}\n"
        "  migrate: {build: ./backend, image: 'liturgikon-migrate:v1'}\n"
        "  db: {image: 'postgres:16'}\n",
        encoding="utf-8",
    )

    assert orchestrator.project_images(compose) == ["liturgikon-backend", "liturgikon-frontend", "liturgikon-migrate"]


def test_a_compose_name_names_the_images(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("name: Iny\nservices:\n  backend: {build: .}\n", encoding="utf-8")

    assert orchestrator.project_images(compose) == ["iny-backend"]


def test_every_tag_of_exactly_those_repositories_is_removed(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs.get("timeout"), "every docker call has a timeout"
        out = {"liturgikon-backend": "liturgikon-backend:latest\nliturgikon-backend:v0.1.0\n"}.get(
            cmd[-1].split("=")[-1], ""
        )
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(orchestrator.subprocess, "run", run)

    assert real_throw_away_project_images(["liturgikon-backend", "liturgikon-test"]) is None

    assert [c[-1] for c in calls[:2]] == ["reference=liturgikon-backend", "reference=liturgikon-test"]
    assert calls[2] == ["docker", "image", "rm", "-f", "liturgikon-backend:latest", "liturgikon-backend:v0.1.0"]


def test_images_that_stayed_are_said(monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(orchestrator.subprocess, "run", run)

    assert real_throw_away_project_images(["liturgikon-backend"]) == (
        "Obrazy projektu ostali na disku (liturgikon-backend): TimeoutExpired."
    )


def test_nothing_to_throw_away_runs_nothing(monkeypatch):
    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *a, **k: pytest.fail("docker ran for no images"))

    assert real_throw_away_project_images([]) is None


def test_the_delete_route_reads_the_images_before_the_workspace_goes():
    """The names come from the project's compose — read after the workspace is removed, there would be none."""
    source = (Path(__file__).resolve().parents[1] / "backend/api/routes/projects.py").read_text(encoding="utf-8")
    body = source[source.index("def delete_project(") :]
    assert body.index("orchestrator.project_images(") < body.index("shutil.rmtree(source_path)")
    assert body.index("ideas_service.back_to_ideas(") < body.index("project_service.delete(db, project_id)")
