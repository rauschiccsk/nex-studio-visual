"""DEV-253 — founding a project on an app template through the real route: the choice is checked, remembered on the
project and handed to the template step with the project's values; a failed step stops the founding and leaves no
project behind. Director 28.10.2026: „pri každom novom projekte by som mal možnosť vybrať vytvoriť vizuál novej
aplikácie podľa nejakého šablónu“."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import app_template
from backend.services import create_project_postscaffold as postscaffold

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


@pytest.fixture(autouse=True)
def _mock_github_validation():
    with patch("backend.services.github_validation.create_github_repo", return_value=None):
        yield


@pytest.fixture()
def manager(db_session, client) -> User:
    from backend.core.security import get_current_user, require_shu_or_above

    user = User(username="zoltan", email="zoltan@isnex.ai", password_hash="x", role="ri")
    db_session.add(user)
    db_session.flush()
    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[require_shu_or_above] = lambda: user
    return user


def payload(manager: User, **changes) -> dict:
    data = {
        "name": "NEX Ukážka",
        "slug": "nex-ukazka",
        "type": "standard",
        "auth_mode": "token",
        "description": "Ukážka šablóny.",
        "repo_url": "rauschiccsk/nex-ukazka",
        "backend_port": 10190,
        "frontend_port": 10191,
        "db_port": 10192,
        "created_by": str(manager.id),
        "template": "nsv-standard",
        "ai": True,
    }
    data.update(changes)
    return data


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error

    def __call__(self, target, choice, **kwargs):
        self.calls.append((target, choice, kwargs))
        if self.error:
            raise self.error
        return "0.3.0"


def test_the_form_is_offered_the_templates(client, manager):
    presets = client.get("/api/v1/projects/create-presets").json()

    assert presets["templates"] == [
        {
            "id": "nsv-standard",
            "label": "NSV štandard",
            "description": "Ľavý panel, prihlásenie, Nastavenia a server ako v NSV — agent stavia len obrazovky "
            "aplikácie.",
        }
    ]
    assert presets["default_template"] == "nsv-standard"


def test_a_project_on_a_template_remembers_it_and_gets_its_frame(client, manager, db_session):
    recorder = Recorder()
    with patch.object(app_template, "apply_template", recorder):
        response = client.post("/api/v1/projects", json=payload(manager))

    assert response.status_code == 201, response.text
    assert response.json()["template"] == "nsv-standard"
    [(target, choice, kwargs)] = recorder.calls
    assert target.name == "nex-ukazka"
    assert choice == app_template.TemplateChoice(template="nsv-standard", ai=True)
    assert kwargs == {
        "name": "NEX Ukážka",
        "slug": "nex-ukazka",
        "auth_mode": "token",
        "ports": (10190, 10191, 10192),
    }


def test_a_project_without_a_template_founds_as_before(client, manager):
    recorder = Recorder()
    with patch.object(app_template, "apply_template", recorder):
        response = client.post("/api/v1/projects", json=payload(manager, template=None, ai=False))

    assert response.status_code == 201, response.text
    assert response.json()["template"] is None
    assert recorder.calls == []


def test_a_failed_template_step_stops_the_founding_with_its_sentence(client, manager, db_session):
    recorder = Recorder(app_template.AppTemplateError("Frontendu šablóny sa nepodarilo pripraviť zoznam balíkov."))
    with patch.object(app_template, "apply_template", recorder):
        response = client.post("/api/v1/projects", json=payload(manager))

    assert response.status_code == 500
    assert response.json()["detail"] == "Frontendu šablóny sa nepodarilo pripraviť zoznam balíkov."
    assert db_session.query(Project).filter_by(slug="nex-ukazka").first() is None


def test_an_adopted_project_keeps_its_own_frame_and_is_told_so(client, manager, tmp_path):
    from backend.api.routes import projects as routes

    recorder = Recorder()
    with (
        patch.object(app_template, "apply_template", recorder),
        patch.object(routes, "_workspace_holds_foreign_files", return_value=True),
        patch.object(postscaffold, "provision_v2_agent_charters") as provision,
    ):
        response = client.post("/api/v1/projects", json=payload(manager, source_path=str(tmp_path / "nex-ukazka")))

    assert response.status_code == 201, response.text
    assert recorder.calls == []
    assert response.json()["template"] is None
    assert routes.ADOPTED_WITHOUT_TEMPLATE in response.json()["setup_warnings"]
    assert provision.call_args.kwargs["template"] is None


def test_the_agents_of_a_project_on_a_template_get_its_rules(client, manager):
    with (
        patch.object(app_template, "apply_template", Recorder()),
        patch.object(postscaffold, "provision_v2_agent_charters") as provision,
    ):
        response = client.post("/api/v1/projects", json=payload(manager))

    assert response.status_code == 201, response.text
    assert provision.call_args.kwargs["template"] == "nsv-standard"


@pytest.mark.parametrize(
    "changes,sentence",
    [
        ({"template": "iny-vzhlad"}, "Šablóna aplikácie „iny-vzhlad“ neexistuje — sú tieto: „nsv-standard“."),
        ({"type": "web"}, "Šablóna aplikácie je zatiaľ len pre bežný projekt — Web projekt sa zakladá bez nej."),
        ({"template": None, "ai": True}, "AI agentov v šablóne si vyber spolu so šablónou aplikácie."),
    ],
)
def test_a_choice_that_does_not_fit_is_said_before_anything_is_created(client, manager, changes, sentence):
    recorder = Recorder()
    with patch.object(app_template, "apply_template", recorder):
        response = client.post("/api/v1/projects", json=payload(manager, **changes))

    assert response.status_code == 422
    assert sentence in response.text
    assert recorder.calls == []
