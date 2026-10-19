"""DEV-149 — a new version gets the number it should, the Manažér sees it before founding, and it stays fixable.

19.10.2026 Dedo's brief for NEX Inbox proposed 1.10.0 (in a sentence). The Manažér clicked „Založiť novú verziu“ and the
cockpit founded 2.0.0: it could not read the number, its own rule rolled 1.9.0 to the next MAJOR, and founding wrote the
Zadanie into ``docs/specs/versions/v2.0.0/`` — which locked the number in the very second the version existed.

Pinned here: the next number bumps the MINOR (KB ``ICC_STANDARDS.md`` §13 — the major changes only when the product
becomes something else); a brief carries its number as data and founding uses the number the Manažér had on screen;
a version not started yet can be renamed, its founding Zadanie moving along.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.config.settings import settings
from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.schemas.version import VersionUpdate
from backend.services import claude_agent, pipeline_runner
from backend.services import version as version_service
from tests.test_dedo_proposal_before_the_work_starts import (
    _TOKEN,
    _ZADANIE,
    _bearer,
    _dedo_auth,
    _make_project,
    _make_user,
)


@pytest.fixture()
def dedo_token(monkeypatch) -> str:
    monkeypatch.setattr(settings, "dedo_api_token_sha256", "")
    monkeypatch.setattr(settings, "dedo_api_token", _TOKEN)
    return _TOKEN


@pytest.fixture()
def no_dispatch(monkeypatch) -> list:
    scheduled: list = []
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda v, d=None: scheduled.append((v, d)))
    return scheduled


def _with_versions(db_session, project, *numbers: str) -> None:
    """Versions created in this order — one transaction would give them all the same ``now()``."""
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for i, number in enumerate(numbers):
        db_session.add(
            Version(
                project_id=project.id,
                version_number=number,
                name=f"v{number}",
                status="released",
                created_at=start + timedelta(minutes=i),
            )
        )
        db_session.flush()


def _brief(client, project_id, *, number: str | None = None):
    body = {"content": _ZADANIE, "proposed_action": "new_version"}
    if number is not None:
        body["proposed_version_number"] = number
    r = client.post(f"/api/v1/dedo/projects/{project_id}/proposals", headers=_dedo_auth(), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _found(client, user, project_id, proposal_id, *, number: str | None = None):
    body = {"proposal_id": str(proposal_id), "text": _ZADANIE}
    if number is not None:
        body["version_number"] = number
    return client.post(f"/api/v1/projects/{project_id}/dedo-proposal/send", headers=_bearer(user), json=body)


def _zadanie(project, number: str):
    return claude_agent.PROJECTS_ROOT / project.slug / f"docs/specs/versions/v{number}/customer-requirements.md"


# --- the next number ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("latest", "expected"),
    [("1.9.0", "1.10.0"), ("1.10.0", "1.11.0"), ("0.9.0", "0.10.0"), ("2.3.4", "2.4.0")],
)
def test_the_next_version_bumps_the_middle_number_and_never_rolls_to_a_new_major(
    db_session, latest: str, expected: str
) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, latest)

    assert version_service.suggest_next_version_number(db_session, project.id) == expected


def test_a_taken_number_is_skipped(db_session) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.10.0", "1.9.0")  # 1.9.0 is the newest, 1.10.0 already exists

    assert version_service.suggest_next_version_number(db_session, project.id) == "1.10.1"


def test_the_new_version_screen_asks_the_cockpit_for_the_number(client, db_session) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()

    r = client.get(f"/api/v1/projects/{project.id}/versions/next-number", headers=_bearer(user))

    assert r.status_code == 200, r.text
    assert r.json() == {"version_number": "1.10.0"}


# --- the number before founding ------------------------------------------------------------------------------------


def test_the_manazer_sees_the_number_dedo_proposed(client, db_session, dedo_token) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()
    _brief(client, project.id, number="1.9.1")

    shown = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user)).json()

    assert shown["proposed_version_number"] == "1.9.1"
    assert shown["version_number"] == "1.9.1"


def test_without_a_proposed_number_the_manazer_sees_the_cockpits_suggestion(client, db_session, dedo_token) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()
    _brief(client, project.id)

    shown = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user)).json()

    assert shown["proposed_version_number"] is None
    assert shown["version_number"] == "1.10.0"


def test_a_fast_fix_shows_no_number_of_its_own(client, db_session, dedo_token) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()
    client.post(
        f"/api/v1/dedo/projects/{project.id}/proposals",
        headers=_dedo_auth(),
        json={"content": _ZADANIE, "proposed_action": "fast_fix", "proposed_version_number": "9.9.9"},
    )

    shown = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user)).json()

    assert shown["version_number"] is None, "a fast fix numbers itself from the finished version"


def test_founding_uses_the_number_dedo_proposed(client, db_session, dedo_token, no_dispatch) -> None:
    """The number Dedo proposed — 1.9.1, which the cockpit would never suggest itself, so the test can tell."""
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()
    brief = _brief(client, project.id, number="1.9.1")

    r = _found(client, user, project.id, brief["id"])

    assert r.status_code == 200, r.text
    version = db_session.get(Version, uuid.UUID(r.json()["version_id"]))
    assert version.version_number == "1.9.1"
    assert _zadanie(project, "1.9.1").read_text(encoding="utf-8").strip() == _ZADANIE.strip()


def test_founding_uses_the_number_the_manazer_changed_it_to(client, db_session, dedo_token, no_dispatch) -> None:
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()
    brief = _brief(client, project.id, number="1.10.0")

    r = _found(client, user, project.id, brief["id"], number="2.0.0")

    assert r.status_code == 200, r.text
    assert db_session.get(Version, uuid.UUID(r.json()["version_id"])).version_number == "2.0.0"


# --- renaming a version not started yet ----------------------------------------------------------------------------


def _founded(client, db_session, dedo_token, no_dispatch, *, number: str = "2.0.0"):
    user = _make_user(db_session)
    project = _make_project(db_session, user, so_semverom=False)
    _with_versions(db_session, project, "1.9.0")
    db_session.commit()
    brief = _brief(client, project.id, number=number)
    r = _found(client, user, project.id, brief["id"])
    assert r.status_code == 200, r.text
    return project, db_session.get(Version, uuid.UUID(r.json()["version_id"]))


def test_a_version_not_started_is_renamed_with_its_zadanie(client, db_session, dedo_token, no_dispatch) -> None:
    """NEX Inbox 2.0.0 → 1.10.0: the number unlocks and the Zadanie moves to the folder of the new number."""
    project, version = _founded(client, db_session, dedo_token, no_dispatch)

    assert version_service.version_number_lock_reason(db_session, version.id) is None
    version_service.update(db_session, version.id, VersionUpdate(version_number="1.10.0"))

    assert version.version_number == "1.10.0"
    assert _zadanie(project, "1.10.0").read_text(encoding="utf-8").strip() == _ZADANIE.strip()
    assert not _zadanie(project, "2.0.0").parent.exists()


def test_a_rename_onto_an_existing_folder_is_refused(client, db_session, dedo_token, no_dispatch) -> None:
    project, version = _founded(client, db_session, dedo_token, no_dispatch)
    _zadanie(project, "1.10.0").parent.mkdir(parents=True)

    with pytest.raises(ValueError, match="už existuje"):
        version_service.update(db_session, version.id, VersionUpdate(version_number="1.10.0"))
    assert _zadanie(project, "2.0.0").is_file(), "the Zadanie stays where it was"


def test_more_than_the_founding_zadanie_keeps_the_number_locked(client, db_session, dedo_token, no_dispatch) -> None:
    project, version = _founded(client, db_session, dedo_token, no_dispatch)
    (_zadanie(project, "2.0.0").parent / "spec").mkdir()

    assert version_service.version_number_lock_reason(db_session, version.id) is not None
    with pytest.raises(ValueError, match="priečinok"):
        version_service.update(db_session, version.id, VersionUpdate(version_number="1.10.0"))


def test_a_started_build_keeps_the_number_locked_even_with_only_the_zadanie(
    client, db_session, dedo_token, no_dispatch
) -> None:
    _project, version = _founded(client, db_session, dedo_token, no_dispatch)
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
            mode=None,
        )
    )
    db_session.flush()

    reason = version_service.version_number_lock_reason(db_session, version.id)

    assert reason is not None and "stavba" in reason
