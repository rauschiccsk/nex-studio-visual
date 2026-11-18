"""DEV-404 — a deploy records the machine it went to, and the „Inštalácie“ page shows that machine, not the customer's
setting of today; an installation its machine does not have is not shown as running.

16.11.2026 the page showed Career Asistent PROD of ICC s.r.o. as 1.0.0 on MAGER — it was never there: its one PROD
deploy of 15.10.2026 went to ANDROS and the customer's setting moved to MAGER on 31.10. Director 16.11.2026: „Súhlasím,
založ, ale teraz nerob.“; 18.11.2026 in the batch — older records as they are, the page says „stroj nezaznamenaný“.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.deploy import DeployEvent
from backend.services import deploy as deploy_service
from backend.services import installations as I
from tests.test_the_installations_page_shows_every_installation import (  # noqa: F401 — the fixtures by name
    _Answer,
    _customer,
    _project,
    _user,
    asked,
)


def _event(db, customer, environment: str, machine) -> None:
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number="1.0.0",
            environment=environment,
            event_type="deploy",
            status="ok",
            machine=machine,
        )
    )
    db.flush()


@pytest.fixture
def career(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner, f"career-asistent-{uuid.uuid4().hex[:4]}", "Career Asistent")
    customer = _customer(db_session, project, "ICC s.r.o.", "icc")
    customer.prod_host = "mager"  # the customer moved to MAGER after the deploy
    db_session.flush()
    return owner, project, customer


def _prod(db, owner, project) -> dict:
    groups = I.list_installations(db, owner)
    app = next(a for g in groups for a in g["apps"] if a["project_slug"] == project.slug)
    return app["prod"]


def test_a_deploy_recorded_on_andros_is_shown_there_and_says_the_customer_is_to_be_elsewhere(db_session, career):
    owner, project, customer = career
    _event(db_session, customer, "prod", "")

    prod = _prod(db_session, owner, project)

    assert prod["machine"] == "ANDROS"
    assert prod["planned_machine"] == "MAGER"
    assert prod["url"].endswith(".isnex.eu"), "the address on the machine it was deployed to"


def test_a_deploy_that_recorded_no_machine_says_so_and_guesses_nothing(db_session, career):
    owner, project, customer = career
    _event(db_session, customer, "prod", None)

    prod = _prod(db_session, owner, project)

    assert prod["machine"] is None
    assert prod["planned_machine"] is None


def test_a_deploy_on_the_customers_machine_names_it_and_nothing_else(db_session, career):
    owner, project, customer = career
    _event(db_session, customer, "prod", "mager")

    prod = _prod(db_session, owner, project)

    assert (prod["machine"], prod["planned_machine"]) == ("MAGER", None)
    assert prod["url"].endswith(".icc.sk")


def test_a_uat_runs_on_the_cockpits_machine(db_session, career):
    owner, project, customer = career
    _event(db_session, customer, "uat", None)

    groups = I.list_installations(db_session, owner)
    uat = next(a for g in groups for a in g["apps"] if a["project_slug"] == project.slug)["uat"]

    assert (uat["machine"], uat["planned_machine"]) == ("ANDROS", None)


async def test_an_installation_its_machine_does_not_have_is_not_shown_running(
    db_session,
    career,
    asked,  # noqa: F811 — the fixture by name
    monkeypatch,
) -> None:
    owner, project, customer = career
    _event(db_session, customer, "prod", None)
    calls, answers = asked
    answers["next"] = _Answer(404, None, "text/plain")
    looked: list = []
    monkeypatch.setattr(
        I, "_present", lambda c, p, environment, machine: looked.append((environment, machine)) or False
    )

    result = await I.status(db_session, owner, customer.id, "prod")

    assert (result.state, result.detail) == ("down", I.NOT_THERE.format(machine="MAGER"))
    assert looked == [("prod", "mager")], "not recorded — looked for on the customer's machine"


async def test_an_installation_that_answers_is_not_looked_for(
    db_session,
    career,
    asked,  # noqa: F811 — the fixture by name
    monkeypatch,
) -> None:
    owner, project, customer = career
    _event(db_session, customer, "prod", "")
    calls, answers = asked
    answers["next"] = _Answer(200, {"status": "ok", "version": "1.0.0"})
    monkeypatch.setattr(I, "_present", lambda *a: pytest.fail("asked although it answers"))

    result = await I.status(db_session, owner, customer.id, "prod")

    assert result.state == "up"
    assert calls[0].endswith(".isnex.eu/api/v1/health"), "asked where it was deployed"


@pytest.mark.asyncio
async def test_a_deploy_records_the_machine_it_went_to(db_session, monkeypatch) -> None:
    from tests.test_deploy_service import _make_customer, _make_project, _make_user, _make_version

    user = _make_user(db_session)
    project = _make_project(db_session, user=user, slug=f"nex-legal-watch-{uuid.uuid4().hex[:6]}")
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)

    async def _runner(**_kwargs):
        return deploy_service.RunnerResult(True, "OK", "https://uat-andros-legal-watch.isnex.eu", [])

    event, _url, _bumped = await deploy_service.deploy(
        db_session, customer.id, version_number="0.1.0", environment="uat", actor_id=user.id, deploy_runner=_runner
    )

    assert event.machine == ""


def test_an_installation_on_this_machine_is_its_folder(db_session, career, tmp_path, monkeypatch) -> None:
    _owner, project, customer = career
    monkeypatch.setattr(I.orchestrator, "PROD_ROOT", tmp_path)

    assert I._present(customer, project, "prod", "") is False
    folder = tmp_path / "icc" / project.slug
    folder.mkdir(parents=True)
    (folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert I._present(customer, project, "prod", "") is True


@pytest.mark.parametrize(
    ("read", "present"), [(("services: {}\n", None), True), ((None, None), False), ((None, "x"), None)]
)
def test_an_installation_on_another_machine_is_read_there(db_session, career, monkeypatch, read, present) -> None:
    _owner, project, customer = career
    asked_where: list = []

    def _read(instance_dir, filename, *, deploy_host, timeout=120):
        asked_where.append((str(instance_dir), filename, deploy_host))
        return read

    monkeypatch.setattr(I.remote_instance, "read_text", _read)

    assert I._present(customer, project, "prod", "mager") is present
    assert asked_where == [(f"/opt/customers/icc/{project.slug}", "docker-compose.yml", "mager")]
