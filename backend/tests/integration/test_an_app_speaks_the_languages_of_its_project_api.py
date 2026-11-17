"""DEV-395 — the languages of a project through the routes the screens use: founding, the project's settings, the
customer's starting language, the kit's upgrade beside the languages, the deploy and Verifikácia.

Director 15.11.2026, Orthodox Hermenia: „Viacjazyčné aplikácie budeme potrebovať … každý užívateľ môže nastaviť pre
seba jazyk aplikácie.“ — „Áno založ všetky štyri a začni ich implementovať.“
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import app_languages, app_template, orchestrator
from backend.services import create_project_postscaffold as postscaffold
from backend.tests.integration.test_create_project_on_a_template import (  # noqa: F401 — fixtures are used by name
    Recorder,
    _mock_github_validation,
    manager,
    payload,
)
from backend.tests.test_a_template_project_upgrades_its_kit import seed

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


def _project(db, tmp_path, *, template: str | None = "nsv-standard", languages=("sk",), kit: str = "0.14.1") -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"lang_{s}", email=f"lang_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Hermenia {s}",
        slug=f"hermenia-{s}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=user.id,
        source_path=str(seed(tmp_path, frame=kit, server=kit)),
        template=template,
        languages=list(languages),
    )
    db.add(project)
    db.flush()
    return project


# --- founding --------------------------------------------------------------------------------------------------------


def test_a_project_is_founded_speaking_its_languages(client, manager):  # noqa: F811
    recorder = Recorder()
    with (
        patch.object(app_template, "apply_template", recorder),
        patch.object(postscaffold, "provision_v2_agent_charters") as provision,
    ):
        response = client.post("/api/v1/projects", json=payload(manager, languages=["hu", "sk"]))

    assert response.status_code == 201, response.text
    assert response.json()["languages"] == ["hu", "sk"]
    [(_target, choice, _kwargs)] = recorder.calls
    assert choice == app_template.TemplateChoice(template="nsv-standard", ai=True, languages=("hu", "sk"))
    assert provision.call_args.kwargs["languages"] == ["hu", "sk"]


def test_a_project_without_the_template_cannot_speak_another_language(client, manager):  # noqa: F811
    recorder = Recorder()
    with patch.object(app_template, "apply_template", recorder):
        response = client.post("/api/v1/projects", json=payload(manager, template=None, ai=False, languages=["hu"]))

    assert response.status_code == 422
    assert "Inými jazykmi než slovenčinou hovorí len aplikácia na šablóne aplikácie" in response.text
    assert recorder.calls == []


# --- the project's settings ----------------------------------------------------------------------------------------


def test_the_languages_are_changed_in_the_settings(client, db_session, tmp_path):
    project = _project(db_session, tmp_path)

    response = client.patch(f"/api/v1/projects/{project.id}", json={"languages": ["hu", "sk"]})

    assert response.status_code == 200, response.text
    assert response.json()["languages"] == ["hu", "sk"]


def test_an_old_kit_is_upgraded_before_the_languages(client, db_session, tmp_path):
    project = _project(db_session, tmp_path, kit="0.13.0")

    response = client.patch(f"/api/v1/projects/{project.id}", json={"languages": ["hu", "sk"]})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Jazyky maďarčina, slovenčina pozná šablóna od verzie nex-app-kit 0.14.1, projekt stojí na 0.13.0. Najprv "
        "povýš šablónu aplikácie (tlačidlo pri jazykoch), potom jazyky ulož."
    )


def test_a_project_without_the_template_keeps_slovak(client, db_session, tmp_path):
    project = _project(db_session, tmp_path, template=None)

    response = client.patch(f"/api/v1/projects/{project.id}", json={"languages": ["hu"]})

    assert response.status_code == 422
    assert "Inými jazykmi než slovenčinou hovorí len aplikácia na šablóne aplikácie" in response.json()["detail"]


def test_a_language_a_customer_starts_in_is_not_taken_away(client, db_session, tmp_path):
    project = _project(db_session, tmp_path, languages=("hu", "sk"))
    db_session.add(Customer(project_id=project.id, name="SKIT o.z.", slug="skit", language="sk"))
    db_session.flush()

    response = client.patch(f"/api/v1/projects/{project.id}", json={"languages": ["hu"]})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Jazyk, ktorý aplikácia prestane hovoriť, majú nastavení títo zákazníci: SKIT o.z. (slovenčina). Najprv im "
        "na obrazovke Zákazníci zmeň jazyk inštalácie."
    )


# --- the customer --------------------------------------------------------------------------------------------------


def test_the_customer_chooses_one_of_the_apps_languages(client, db_session, tmp_path):
    project = _project(db_session, tmp_path, languages=("hu", "sk"))
    created = client.post(
        f"/api/v1/projects/{project.slug}/customers", json={"name": "SKIT o.z.", "slug": "skit", "language": "sk"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["language"] == "sk"

    refused = client.patch(f"/api/v1/customers/{created.json()['id']}", json={"language": "de"})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Aplikácia nehovorí jazykom „de“ — hovorí: maďarčina (hlavný jazyk), slovenčina."

    cleared = client.patch(f"/api/v1/customers/{created.json()['id']}", json={"language": ""})
    assert cleared.status_code == 200
    assert cleared.json()["language"] == ""


def test_the_form_is_offered_the_apps_languages_only_where_there_is_a_choice(client, db_session, tmp_path):
    multilingual = _project(db_session, tmp_path / "a", languages=("hu", "sk"))
    slovak = _project(db_session, tmp_path / "b")

    offered = client.get(f"/api/v1/projects/{multilingual.slug}/customer-settings").json()["languages"]
    none = client.get(f"/api/v1/projects/{slovak.slug}/customer-settings").json()["languages"]

    assert offered == [{"code": "hu", "label": "maďarčina"}, {"code": "sk", "label": "slovenčina"}]
    assert none == []


# --- the kit's upgrade beside the languages ------------------------------------------------------------------------


def test_the_kit_waits_while_an_agent_works_in_the_checkout(client, db_session, tmp_path):
    project = _project(db_session, tmp_path, kit="0.13.0")
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
        )
    )
    db_session.flush()

    response = client.post(f"/api/v1/projects/{project.id}/app-kit-upgrade", json={"target_version": "0.14.1"})

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Agent práve pracuje na verzii 0.1.0 — šablónu povýš, keď dokončí ťah")


def test_the_status_says_whether_the_kit_knows_languages(client, db_session, tmp_path, monkeypatch):
    from backend.services import nexshared

    monkeypatch.setattr(nexshared, "list_remote_tags", lambda *a, **k: ["0.13.0", "0.14.1", "0.15.0"])
    monkeypatch.setattr(nexshared, "fetch_changelog", lambda *a, **k: "")
    old = _project(db_session, tmp_path / "old", kit="0.13.0")
    middle = _project(db_session, tmp_path / "middle", kit="0.14.1")
    new = _project(db_session, tmp_path / "new", kit="0.15.0")

    assert client.get(f"/api/v1/projects/{old.id}/app-kit-status").json()["languages_known"] == []
    assert client.get(f"/api/v1/projects/{middle.id}/app-kit-status").json()["languages_known"] == ["sk", "hu"]
    body = client.get(f"/api/v1/projects/{new.id}/app-kit-status").json()
    assert body["languages_known"] == ["sk", "hu", "en"]
    assert body["languages_from"] == {"sk": "0.14.1", "hu": "0.14.1", "en": "0.15.0"}


# --- the deploy ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "languages,customer_language,told",
    [(("hu", "sk"), "sk", "sk"), (("hu", "sk"), None, "hu"), (("sk",), None, None)],
)
async def test_the_deploy_tells_the_installation_its_language(
    db_session, monkeypatch, tmp_path, languages, customer_language, told
):
    from backend.services import ci_status
    from backend.services import deploy as deploy_service
    from tests import test_red_ci_after_the_gate_stops_the_deploy as red_ci

    handed: dict = {}

    async def _runner(**kwargs):
        handed.update(kwargs)
        return True, "OK", "https://uat-skit-hermenia.isnex.eu"

    async def _green(project_root, sha=None, *, cerstvy=False):
        return red_ci._ci("green", detail="CI zelené")

    monkeypatch.setattr(red_ci.orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    project, _version = red_ci._finished_version(db_session)
    project.template, project.languages = "nsv-standard", list(languages)
    customer = red_ci.customer_service.create(
        db_session, project.id, red_ci.CustomerCreate(name="SKIT o.z.", slug="skit", language=customer_language)
    )
    monkeypatch.setattr(red_ci.orchestrator, "_project_has_ci", lambda root: False)
    monkeypatch.setattr(ci_status, "stav_commitu", _green)

    await deploy_service.deploy(
        db_session, customer.id, version_number="1.7.0", environment="uat", actor_id=project.created_by,
        deploy_runner=_runner,
    )  # fmt: skip

    assert handed.get("installation_language") == told
    assert ("installation_language" in handed) is (told is not None)


# --- Verifikácia ---------------------------------------------------------------------------------------------------


def _version_of(db, project: Project) -> Version:
    version = Version(project_id=project.id, version_number="0.2.0", status="active")
    db.add(version)
    db.flush()
    return version


async def test_verifikacia_stops_an_app_that_does_not_speak_every_language(db_session, tmp_path, monkeypatch):
    project = _project(db_session, tmp_path, languages=("hu", "sk"))
    version = _version_of(db_session, project)
    asked: list = []

    def measured(root, languages):
        asked.append(tuple(languages))
        return ["v jazyku slovenčina chýba preklad textov „app.save“ (frontend/src/locales/sk.ts)"], []

    monkeypatch.setattr(app_languages, "problems", measured)

    finding, notes = await orchestrator._app_languages_check(db_session, version.id, project.slug)

    assert asked == [("hu", "sk")]
    assert notes == []
    assert finding == {
        "text": "Aplikácia má hovoriť: maďarčina (hlavný jazyk), slovenčina — v jazyku slovenčina chýba preklad textov "
        "„app.save“ (frontend/src/locales/sk.ts). Oprav všetko naraz (pravidlá agenta, časť „Aplikácia hovorí "
        "jazykmi projektu“).",
        "blocking": True,
        "escapes_tests": False,
    }


async def test_verifikacia_does_not_measure_a_slovak_app(db_session, tmp_path, monkeypatch):
    project = _project(db_session, tmp_path)
    version = _version_of(db_session, project)
    monkeypatch.setattr(app_languages, "problems", lambda *a: pytest.fail("a Slovak app is not measured"))

    assert await orchestrator._app_languages_check(db_session, version.id, project.slug) == (None, [])


async def test_a_pass_of_the_auditor_is_floored_by_a_missing_translation(db_session, monkeypatch):
    """The finding stops the version — the round's verdict is FAIL even when the Auditor passed it, the Manažér reads
    why, and a part of the check that could not run is said with the verdict."""
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia, _stub_smoke, _verdict_pass
    from tests.test_the_verdict_is_said_once_with_the_cockpits_findings import _auditor_that_writes, _cockpit_adds

    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _stub_smoke(monkeypatch)
    _auditor_that_writes(monkeypatch, _verdict_pass())
    _cockpit_adds(monkeypatch)
    finding = {"text": "Aplikácia má hovoriť: maďarčina — chýba katalóg.", "blocking": True, "escapes_tests": False}
    note = "Kontrola jazykov aplikácie: preklady a texty v kóde sa nezmerali — GitHub neodpovedal."

    async def _languages(db, version_id, slug):
        return finding, [note]

    monkeypatch.setattr(orchestrator, "_app_languages_check", _languages)

    await orchestrator.run_dispatch(db_session, version.id)

    [verdict] = [
        m
        for m in db_session.query(PipelineMessage).filter_by(version_id=version.id).all()
        if m.author == "auditor" and m.kind == "verdict"
    ]
    assert verdict.payload["verdict"] == "FAIL"
    assert verdict.payload["engine_override"] == "app_languages"
    assert finding in verdict.payload["findings"]
    assert verdict.content.startswith(
        "Verzia funguje, ale nehovorí všetkými jazykmi projektu — treba doplniť preklady."
    )
    assert verdict.content.endswith(note)
