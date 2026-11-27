"""DEV-534 — a screenshot pasted into the conversation with the Staviteľ goes as in Nápady: with the message, shown at
it, never into the project, to the Staviteľ once, read-only in his build container, gone when the version is.

The Director 27.11.2026: „…ja nechcem aby tieto snímky boli uložené do projektu ako súbory, slúžia len výhradne na to
aby som vysvetli čo presne chcem a potom už netreba.“
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes.pipeline import router as pipeline_router
from backend.config.settings import settings
from backend.core.security import require_shu_or_above
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import build_sandbox, claude_agent, conversation_images, orchestrator, pipeline_runner

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
SHOT = {"name": "Snímka 1.png", "data": base64.b64encode(PNG).decode()}
TEXT = {"name": "poznamka.png", "data": base64.b64encode(b"len text, nie obrazok").decode()}


@pytest.fixture(autouse=True)
def images_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "conversation_images_dir", str(tmp_path / "conversation-images"))
    return tmp_path / "conversation-images"


def _version(db_session, user: User) -> tuple[Version, Project]:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().int % 10**4}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _state(db_session, version_id, **fields) -> PipelineState:
    values = {
        "version_id": version_id,
        "flow_type": "new_version",
        "current_stage": "vizual",
        "current_actor": orchestrator.STAGE_ACTOR["vizual"],
        "status": "agent_working",
        "next_action": "Staviteľ pracuje.",
        "mode": "conversation",
    }
    values.update(fields)
    state = PipelineState(**values)
    db_session.add(state)
    db_session.flush()
    return state


def _manazer_messages(db_session, version_id) -> list[PipelineMessage]:
    return list(
        db_session.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
            .order_by(PipelineMessage.seq)
        ).scalars()
    )


@pytest.fixture()
def client(db_session, monkeypatch):
    async def _idle(*_a, **_k) -> bool:
        return True

    monkeypatch.setattr(pipeline_runner, "idle_within", _idle)
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda vid, directive=None: None)
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1/pipeline")
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.sk", password_hash="x", role="ri"
    )
    db_session.add(user)
    db_session.flush()

    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_shu_or_above] = lambda: user
    with TestClient(app) as c:
        c._user = user
        yield c
    app.dependency_overrides.clear()


def test_a_screenshot_goes_with_the_message_into_the_cockpit_s_data_not_the_project(db_session, client, images_dir):
    version, project = _version(db_session, client._user)
    _state(db_session, version.id)

    r = client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Tlačidlo je vľavo.", "images": [SHOT]})

    assert r.status_code == 200, r.text
    message = _manazer_messages(db_session, version.id)[-1]
    assert message.content == "Tlačidlo je vľavo."
    [meta] = message.payload["images"]
    assert (meta["name"], meta["mime"]) == ("Snímka 1.png", "image/png")
    stored = images_dir / project.slug / str(version.id) / f"{meta['id']}.png"
    assert stored.read_bytes() == PNG

    shown = client.get(f"/api/v1/pipeline/{version.id}/screenshots/{meta['id']}")
    assert (shown.status_code, shown.headers["content-type"], shown.content) == (200, "image/png", PNG)


def test_uprav_carries_its_screenshots_and_the_engine_never_sees_them(db_session, client, monkeypatch):
    version, _ = _version(db_session, client._user)
    _state(db_session, version.id, status="awaiting_manazer", next_action="Pozri vizuál.")
    seen: list[dict] = []
    real = orchestrator.apply_action

    async def _spy(db, *, version_id, action, payload=None, acting_user=None):
        seen.append(dict(payload or {}))
        return await real(db, version_id=version_id, action=action, payload=payload, acting_user=acting_user)

    monkeypatch.setattr(orchestrator, "apply_action", _spy)

    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "uprav", "payload": {"comment": "Posuň ho doprava.", "images": [SHOT]}},
    )

    assert r.status_code == 200, r.text
    assert seen == [{"comment": "Posuň ho doprava."}]
    assert _manazer_messages(db_session, version.id)[-1].payload["images"][0]["mime"] == "image/png"


def test_something_that_is_not_an_image_is_refused_and_nothing_is_sent(db_session, client, images_dir):
    version, _ = _version(db_session, client._user)
    _state(db_session, version.id)

    r = client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pozri.", "images": [TEXT]})

    assert r.status_code == 400
    assert r.json()["detail"] == conversation_images.NOT_AN_IMAGE.format(name="poznamka.png")
    assert _manazer_messages(db_session, version.id) == []
    assert not any(images_dir.rglob("*.*"))


def test_a_message_the_engine_refuses_leaves_no_screenshot_behind(db_session, client, images_dir):
    version, _ = _version(db_session, client._user)
    _state(db_session, version.id, status="blocked", block_reason="framework_issue", current_stage="programovanie")

    r = client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pokračuj", "images": [SHOT]})

    assert r.status_code == 400
    assert not any(images_dir.rglob("*.png"))


def test_a_screenshot_of_another_version_is_not_given(db_session, client):
    version, _ = _version(db_session, client._user)
    other, _ = _version(db_session, client._user)
    _state(db_session, version.id)
    client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pozri.", "images": [SHOT]})
    image_id = _manazer_messages(db_session, version.id)[-1].payload["images"][0]["id"]

    assert client.get(f"/api/v1/pipeline/{other.id}/screenshots/{image_id}").status_code == 404


def test_the_box_is_told_poradca_s_limits_for_images_only(db_session, client):
    version, _ = _version(db_session, client._user)
    limits = client.get(f"/api/v1/pipeline/{version.id}/screenshot-limits").json()
    assert limits == {
        "attachment_max_bytes": settings.poradca_attachment_max_bytes,
        "attachments_max_count": settings.poradca_attachments_max_count,
        "attachments_max_total_bytes": settings.poradca_attachments_max_total_bytes,
        "attachment_types": ["image/png", "image/jpeg", "image/webp", "image/gif"],
    }


async def test_the_staviteľ_s_next_turn_is_told_once_where_they_are(db_session, client, monkeypatch, images_dir):
    version, project = _version(db_session, client._user)
    _state(db_session, version.id)
    client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pozri.", "images": [SHOT]})
    image_id = _manazer_messages(db_session, version.id)[-1].payload["images"][0]["id"]
    prompts: list[str] = []

    async def _claude(**kwargs):
        prompts.append(kwargs["prompt"])
        body = {"stage": "vizual", "kind": "answer", "summary": "ok", "awaiting": "manazer"}
        return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"

    monkeypatch.setattr(orchestrator, "invoke_claude", _claude)
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AUDITOR_ROLE, stage="vizual", prompt="a"
    )
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="vizual", prompt="b"
    )
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="vizual", prompt="c"
    )

    path = images_dir / project.slug / str(version.id) / f"{image_id}.png"
    assert [str(path) in p for p in prompts] == [False, True, False]
    assert "otvor každú nástrojom Read" in prompts[1]


def test_a_build_container_gets_its_project_s_screenshots_read_only(images_dir, monkeypatch):
    # The sandbox maps /opt/projects to the host as it is (test_build_sandbox pins it the same way).
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", Path("/opt/projects"))
    slug = "orthodox-register"
    assert conversation_images.mount_args(slug) == []
    (images_dir / slug).mkdir(parents=True)

    argv = build_sandbox.build_run_argv(project_slug=slug, container_name="c", claude_argv=["claude", "-p", "x"])

    folder = images_dir / slug
    assert f"type=bind,source={folder},target={folder},readonly" in argv
    assert not any(str(images_dir / "iny-projekt") in a for a in argv)


def test_a_finished_version_takes_its_screenshots_with_it(db_session, client, images_dir):
    version, project = _version(db_session, client._user)
    state = _state(db_session, version.id)
    client.post(f"/api/v1/pipeline/{version.id}/relay", json={"text": "Pozri.", "images": [SHOT]})
    other, _ = _version(db_session, client._user)
    kept = images_dir / project.slug / "ina-verzia"
    kept.mkdir(parents=True)
    assert (images_dir / project.slug / str(version.id)).is_dir()

    orchestrator._settle_build_done(db_session, state, "Hotovo.")

    assert not (images_dir / project.slug / str(version.id)).exists()
    assert kept.is_dir()


def test_a_cancelled_version_and_a_deleted_project_take_theirs(images_dir):
    version_id = uuid.uuid4()
    (images_dir / "p-1" / str(version_id)).mkdir(parents=True)
    (images_dir / "p-1" / "ina").mkdir()
    conversation_images.remove_version("p-1", version_id)
    assert sorted(p.name for p in (images_dir / "p-1").iterdir()) == ["ina"]
    conversation_images.remove_project("p-1")
    assert not (images_dir / "p-1").exists()
