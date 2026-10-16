"""DEV-129 — every warning of a deploy is said once, in Slovak, with no technical prefix.

15.10.2026, PROD of NEX Websites: the same warnings stood twice on the page — once in the column of the deployed
version (the report of the last deploy) and once under the deploy button — and the first copy began with the English
„warnings:“. Both runners glued the warnings into the event's text (``detail = f"{detail} | warnings: …"``), the deploy
glued its own pairing warnings after them, and the page split the text back on „ | “ while also listing the click's
``warnings``.

The rule: the event keeps its warnings in a field of their own — never inside ``detail`` — and the matrix hands the page
that field. An event recorded before the field existed is split when it is read (no bulk update of the audit log): its
head is the report, every later part one warning, the „warnings: “ prefix gone. The glued block is kept whole — a
warning may itself hold „; “ (the health regressions, the settings the cockpit filled in), so splitting on it would cut
a sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db.models.deploy import DeployEvent
from backend.services import deploy as deploy_service
from backend.services import orchestrator, uat_provisioner
from tests.test_deploy_service import (
    _make_customer,
    _make_project,
    _make_user,
    _make_version,
)

FIRST = "Prvé upozornenie nasadenia."
SECOND = "Doplnené nastavenia — backend: A; frontend: B."


class _Provisioned:
    warnings = [FIRST, SECOND]
    fe_service = "frontend"
    compose_path = Path("/nonexistent/docker-compose.yml")


@pytest.fixture
def provision_with_warnings(monkeypatch):
    """The provisioner reports two warnings, the stack comes up „OK“ — nothing touches Docker."""
    monkeypatch.setattr(uat_provisioner, "provision_uat", lambda *a, **k: _Provisioned())

    async def _up(*_a, **_k):
        return True, "OK"

    async def _no_regressions(*_a, **_k):
        return []

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _up)
    monkeypatch.setattr(orchestrator, "health_regressions", _no_regressions)


@pytest.mark.asyncio
async def test_the_runner_keeps_the_warnings_out_of_its_report(provision_with_warnings):
    result = await deploy_service._default_deploy_runner(
        project_slug="nex-demo", uat_slug="andros-uat", version_number="v0.1.0", force_fresh=False
    )
    _ok, detail, _url = result
    assert detail == "OK"
    assert result.warnings == [FIRST, SECOND]


@pytest.mark.asyncio
async def test_the_adopting_runner_keeps_the_warnings_out_of_its_report(provision_with_warnings, tmp_path, monkeypatch):
    from backend.services import instance_adoption

    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(instance_adoption, "instance_dir_for", lambda **_: tmp_path)
    monkeypatch.setattr(uat_provisioner, "derive_uat_slug", lambda _: "manager")

    result = await instance_adoption.adopting_deploy_runner(
        project_slug="nex-manager", uat_slug="mager-uat", version_number="1.1.0", force_fresh=False
    )
    _ok, detail, _url = result
    assert FIRST not in detail and SECOND not in detail
    assert "warnings" not in detail
    assert result.warnings == [FIRST, SECOND]


def _runner(*warnings: str):
    async def run(**_kwargs):
        return deploy_service.RunnerResult(True, "OK", "https://uat-andros-demo.isnex.eu", list(warnings))

    return run


@pytest.mark.asyncio
async def test_the_event_keeps_the_warnings_in_their_own_field_and_the_matrix_hands_them_over(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)

    outcome = await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="v0.1.0",
        environment="uat",
        actor_id=user.id,
        deploy_runner=_runner(FIRST, SECOND),
    )
    db_session.flush()
    db_session.expire_all()
    event = db_session.get(DeployEvent, outcome[0].id)

    assert event.detail == "OK"
    assert event.warnings == [FIRST, SECOND]
    row = deploy_service.build_matrix(db_session, project)["rows"][0]
    assert (row["uat_last_deploy_detail"], row["uat_last_deploy_warnings"]) == ("OK", [FIRST, SECOND])
    assert (row["prod_last_deploy_detail"], row["prod_last_deploy_warnings"]) == (None, [])


@pytest.mark.asyncio
async def test_a_warning_found_after_the_event_was_written_is_kept_too(db_session, monkeypatch):
    """The ticket of a fast fix follows its deploy only after the event exists (DEV-122) — its warning still counts."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    version = _make_version(db_session, project, "v0.1.0")
    version.plane_issue_id = "issue-1"
    customer = _make_customer(db_session, project)
    late = "Tiket opravy sa nepohol — Plane neodpovedal."

    async def _plane_silent(*_a, **_k):
        return late

    monkeypatch.setattr(deploy_service, "_follow_fix_ticket", _plane_silent)

    outcome = await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="v0.1.0",
        environment="uat",
        actor_id=user.id,
        deploy_runner=_runner(FIRST),
    )
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(DeployEvent, outcome[0].id).warnings == [FIRST, late]
    assert outcome.warnings == [FIRST, late]


@pytest.mark.parametrize(
    ("detail", "report", "warnings"),
    [
        ("OK", "OK", []),
        (f"OK | warnings: {FIRST}", "OK", [FIRST]),
        (f"OK | warnings: {FIRST}; {SECOND} | Párovanie chýba.", "OK", [f"{FIRST}; {SECOND}", "Párovanie chýba."]),
        ("OK | Párovanie chýba.", "OK", ["Párovanie chýba."]),
    ],
)
def test_an_event_from_before_the_field_is_split_when_read(db_session, detail, report, warnings):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    customer = _make_customer(db_session, project)
    db_session.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number="v0.1.0",
            environment="prod",
            event_type="deploy",
            status="ok",
            detail=detail,
        )
    )
    db_session.flush()

    row = deploy_service.build_matrix(db_session, project)["rows"][0]
    assert (row["prod_last_deploy_detail"], row["prod_last_deploy_warnings"]) == (report, warnings)
