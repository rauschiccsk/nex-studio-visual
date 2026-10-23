"""DEV-206 — Dedo's brief for a new version fills a version that has not started yet instead of founding another.

A new project gets its 0.1.0 when it is founded — planned, no build, no Zadanie. A ``new_version`` brief always founded
a NEW version: with the number 0.1.0 the founding failed (the number was taken), without it the cockpit proposed 0.2.0
— the work would go into 0.2.0 and the empty 0.1.0 would stay behind. Measured 23.10.2026 on NEX Knowledge before its
first brief. Director 23.10.2026: „Áno, súhlasím, založ tiket a oprav to“.

A version that has begun — a build record, a Zadanie on disk, or past ``planned`` — is never filled (ICCINT-71: the
brief must not overwrite work).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.services import version as version_service
from tests.test_dedo_proposal_before_the_work_starts import (  # noqa: F401 — fixtures
    _ZADANIE,
    _bearer,
    _make_project,
    _make_user,
    _navrhni,
    _posli,
    dedo_token,
    no_dispatch,
)

HAND_WRITTEN = "Zadanie, ktoré Manažér napísal sám."


def _new_project(db_session):
    """A project as the cockpit founds it: its only version is the planned 0.1.0."""
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    first = Version(project_id=project.id, version_number="0.1.0", name="Initial prototype")
    db_session.add(first)
    db_session.commit()
    return user, project, first


def _versions(db_session, project_id) -> list[str]:
    db_session.expire_all()
    return sorted(db_session.execute(select(Version.version_number).where(Version.project_id == project_id)).scalars())


def _open_brief(client, user, project_id) -> dict:
    answer = client.get(f"/api/v1/projects/{project_id}/dedo-proposal", headers=_bearer(user))
    assert answer.status_code == 200, answer.text
    return answer.json()


def _send(client, user, project_id, proposal_id, number: str | None):
    return client.post(
        f"/api/v1/projects/{project_id}/dedo-proposal/send",
        headers=_bearer(user),
        json={"proposal_id": str(proposal_id), "text": _ZADANIE, "work_kind": None, "version_number": number},
    )


def test_the_brief_offers_the_unstarted_version(client, db_session, dedo_token):  # noqa: F811
    user, project, _first = _new_project(db_session)
    _navrhni(client, project.id, action="new_version")

    brief = _open_brief(client, user, project.id)

    assert (brief["version_number"], brief["unstarted_version_numbers"]) == ("0.1.0", ["0.1.0"])


@pytest.mark.parametrize("number", [None, "0.1.0"], ids=["the offered number", "the number typed"])
def test_sending_it_fills_the_unstarted_version(client, db_session, dedo_token, no_dispatch, number):  # noqa: F811
    user, project, first = _new_project(db_session)
    brief = _navrhni(client, project.id, action="new_version").json()

    answer = _send(client, user, project.id, brief["id"], number)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"version_id": str(first.id), "started": False}
    assert _versions(db_session, project.id) == ["0.1.0"], "a second version was founded next to the empty 0.1.0"
    assert version_service.read_zadanie(db_session, first.id).strip() == _ZADANIE
    filled = db_session.get(Version, first.id)
    assert (filled.status, filled.description, filled.name) == ("planned", _ZADANIE, "Initial prototype")
    assert db_session.execute(select(PipelineState)).scalars().all() == [] and no_dispatch == []


def _begun_by_a_build(db_session, version):
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="awaiting_manazer",
        )
    )


def _begun_by_a_zadanie(db_session, version):
    version_service.write_zadanie(db_session, version.id, HAND_WRITTEN)


def _begun_past_planned(db_session, version):
    version.status = "active"


@pytest.mark.parametrize(
    "begin",
    [_begun_by_a_build, _begun_by_a_zadanie, _begun_past_planned],
    ids=["a build record", "a Zadanie on disk", "past planned"],
)
def test_a_version_that_has_begun_is_never_filled(client, db_session, dedo_token, no_dispatch, begin):  # noqa: F811
    user, project, first = _new_project(db_session)
    begin(db_session, first)
    db_session.commit()
    zadanie_before = version_service.read_zadanie(db_session, first.id)
    brief = _navrhni(client, project.id, action="new_version").json()

    offered = _open_brief(client, user, project.id)
    answer = _send(client, user, project.id, brief["id"], "0.1.0")

    assert (offered["version_number"], offered["unstarted_version_numbers"]) == ("0.2.0", [])
    assert answer.status_code in (400, 409), answer.text
    assert _versions(db_session, project.id) == ["0.1.0"]
    assert version_service.read_zadanie(db_session, first.id) == zadanie_before
    assert db_session.get(Version, first.id).description in (None, "")


def test_another_number_still_founds_a_new_version(client, db_session, dedo_token, no_dispatch):  # noqa: F811
    """The Manažér chose a different number — the empty 0.1.0 is left alone and the new one is founded as before."""
    user, project, first = _new_project(db_session)
    brief = _navrhni(client, project.id, action="new_version").json()

    answer = _send(client, user, project.id, brief["id"], "0.2.0")

    assert answer.status_code == 200, answer.text
    assert _versions(db_session, project.id) == ["0.1.0", "0.2.0"]
    assert answer.json()["version_id"] != str(first.id)
    assert version_service.read_zadanie(db_session, first.id) == ""


def test_a_project_without_an_unstarted_version_gets_the_next_number(client, db_session, dedo_token):  # noqa: F811
    user = _make_user(db_session)
    project = _make_project(db_session, user)  # its only version, 1.5.5, is released
    db_session.commit()
    _navrhni(client, project.id, action="new_version")

    brief = _open_brief(client, user, project.id)

    assert (brief["version_number"], brief["unstarted_version_numbers"]) == ("1.6.0", [])


def test_the_unstarted_versions_come_in_number_order(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    for number in ("0.10.0", "0.2.0", "0.9.1"):
        db_session.add(Version(project_id=project.id, version_number=number, name=f"v{number}"))
    db_session.flush()

    assert [v.version_number for v in version_service.unstarted_versions(db_session, project.id)] == [
        "0.2.0",
        "0.9.1",
        "0.10.0",
    ]


def test_an_unknown_project_has_none(db_session):
    assert version_service.unstarted_versions(db_session, uuid.uuid4()) == []
