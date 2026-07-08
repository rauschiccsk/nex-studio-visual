"""Integration tests for the change-request capture + version mint (konzultacia-mode.md Part 2; -followup Fix 3/4).

A read-only Konzultácia turn that raises a ``change_request`` marker routes into a NEW version: the capture
endpoint records a project backlog ``REQ-N`` AND mints the NEXT version in DRAFT (``planned``, NO
``PipelineState``, NO build running), linking the REQ to it — but NEVER auto-starts a build.

konzultacia-followup.md Fix 3/4 tightens the contract:
  * capture is keyed on the SOURCE consult message (``message_id``) and is IDEMPOTENT — a second capture of an
    already-captured marker returns the EXISTING version (no duplicate 1.1.0 / 1.2.0 mints);
  * the source message must be a terminal consult turn (``stage == 'done'``) — a mid-build marker is rejected;
  * the endpoint returns ``project_slug`` (the FE navigates using the RETURNED slug).

Run against the real v2 DB (test DB :9178, SAVEPOINT-isolated ``db_session``); ``write_zadanie`` is patched
to a no-op so the test never touches the real ``/opt/projects`` filesystem.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import func, select

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import change_request as change_request_service
from backend.services import version as version_service

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"cc_{_uuid.uuid4().hex[:8]}",
        email=f"cc_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"CR Proj {suffix}",
        slug=f"cr-{suffix}",
        type="standard",
        auth_mode="password",
        description="Change-request Part 2 test project.",
        created_by=creator.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, *, status: str = "released", version_number: str = "1.0.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status=status)
    db.add(version)
    db.flush()
    return version


def _seed_consult_message(
    db, version: Version, *, summary: str, title: str | None = None, stage: str = "done"
) -> PipelineMessage:
    """A read-only consult answer carrying a ``change_request`` marker (the source of a capture). ``stage`` is
    'done' for a real consult turn; a non-'done' stage seeds the mid-build rejection case."""
    marker: dict = {"summary": summary}
    if title is not None:
        marker["title"] = title
    msg = PipelineMessage(
        version_id=version.id,
        stage=stage,
        author="ai_agent",
        recipient="manazer",
        kind="answer",
        content=summary,
        status="delivered",
        payload={"consult": True, "phase": stage, "change_request": marker},
    )
    db.add(msg)
    db.flush()
    return msg


@pytest.fixture(autouse=True)
def _no_disk_zadanie(monkeypatch):
    """Stub write_zadanie so the mint never writes customer-requirements.md to the real /opt/projects tree."""
    monkeypatch.setattr(version_service, "write_zadanie", lambda db, version_id, content: "customer-requirements.md")


# ---------------------------------------------------------------------------
# (i) capture records a backlog REQ-N + mints a DRAFT next version linked to it, NO build
# ---------------------------------------------------------------------------


def test_capture_mints_draft_version_and_backlog_req(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project, version_number="1.0.0")
    msg = _seed_consult_message(db_session, consulted, summary="Pridať export faktúr do XLSX.", title="XLSX export")

    result = change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)

    assert result.created is True
    assert result.version_number == "1.1.0"  # bumped past 1.0.0
    assert result.project_slug == project.slug  # Fix 4: the returned slug drives FE navigation

    # A DRAFT (planned) next version was minted — NOT active/released.
    new_version = db_session.get(Version, result.version_id)
    assert new_version is not None
    assert new_version.status == "planned"
    assert new_version.project_id == project.id
    assert new_version.description == "Pridať export faktúr do XLSX."

    # NO PipelineState — the build is NOT running; it begins only when the Manažér opens the version (Part 2.3).
    assert (
        db_session.execute(select(PipelineState).where(PipelineState.version_id == new_version.id)).scalar_one_or_none()
        is None
    )

    # A backlog REQ-N was recorded and LINKED to the new version (status='included').
    req = db_session.execute(
        select(BacklogItem).where(BacklogItem.project_id == project.id, BacklogItem.number == result.backlog_number)
    ).scalar_one()
    assert req.title == "XLSX export"
    assert req.description == "Pridať export faktúr do XLSX."
    assert req.version_id == new_version.id
    assert req.status == "included"

    # The source marker was stamped so a repeat capture is idempotent + the FE bar hides.
    db_session.refresh(msg)
    assert msg.payload["change_request"]["captured_version_id"] == str(new_version.id)


# ---------------------------------------------------------------------------
# (ii) title falls back to the summary; the version number rolls major at .10
# ---------------------------------------------------------------------------


def test_capture_title_fallback_and_major_roll(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project, version_number="1.9.0")
    msg = _seed_consult_message(
        db_session, consulted, summary="Zmena správania pri duplicitných faktúrach.", title=None
    )

    result = change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)

    assert result.version_number == "2.0.0"  # 1.9.0 → 2.0.0 (minor+1 >= 10 rolls the major)
    req = db_session.execute(
        select(BacklogItem).where(BacklogItem.project_id == project.id, BacklogItem.number == result.backlog_number)
    ).scalar_one()
    assert req.title == "Zmena správania pri duplicitných faktúrach."  # no title → derives from the summary


# ---------------------------------------------------------------------------
# (iii) idempotency: a second capture of the SAME source marker mints exactly ONE version
# ---------------------------------------------------------------------------


def test_double_capture_is_idempotent(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project, version_number="1.0.0")
    msg = _seed_consult_message(db_session, consulted, summary="Pridať filtrovanie.", title=None)

    versions_before = db_session.execute(
        select(func.count()).select_from(Version).where(Version.project_id == project.id)
    ).scalar_one()

    first = change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)
    second = change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)

    assert first.created is True
    assert second.created is False  # idempotent replay — the EXISTING version, no new mint
    assert second.version_id == first.version_id
    assert second.version_number == first.version_number
    assert second.backlog_number == first.backlog_number

    # Exactly ONE new version was minted across the two captures (consulted + the single draft).
    versions_after = db_session.execute(
        select(func.count()).select_from(Version).where(Version.project_id == project.id)
    ).scalar_one()
    assert versions_after == versions_before + 1


# ---------------------------------------------------------------------------
# (iv) rejections: blank summary (422), non-terminal source (422), unknown message (404)
# ---------------------------------------------------------------------------


def test_capture_rejects_blank_summary(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project)
    msg = _seed_consult_message(db_session, consulted, summary="   ", title=None)

    with pytest.raises(ValueError, match="non-empty summary"):
        change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)


def test_capture_rejects_non_terminal_source(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project)
    # A marker on a MID-BUILD turn (stage != 'done') must never mint a version (gate the marker to terminal).
    msg = _seed_consult_message(db_session, consulted, summary="Rozbiť build.", stage="programovanie")

    with pytest.raises(ValueError, match="not a finished consult"):
        change_request_service.capture(db_session, source_message_id=msg.id, user_id=creator.id)


def test_capture_unknown_message_raises(db_session) -> None:
    creator = _seed_user(db_session)
    with pytest.raises(ValueError, match="not found"):
        change_request_service.capture(db_session, source_message_id=_uuid.uuid4(), user_id=creator.id)


# ---------------------------------------------------------------------------
# (v) the endpoint captures + returns slug/number/id (no build) and is idempotent per source message
# ---------------------------------------------------------------------------


def test_change_request_endpoint(client, db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    consulted = _seed_version(db_session, project, version_number="2.3.0")
    msg = _seed_consult_message(
        db_session, consulted, summary="Pridať filtrovanie podľa dátumu.", title="Filter dátumu"
    )

    resp = client.post(
        f"/api/v1/pipeline/{consulted.id}/change-request",
        json={"message_id": str(msg.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version_number"] == "2.4.0"
    assert body["project_slug"] == project.slug  # Fix 4
    assert body["backlog_number"] >= 1

    minted = db_session.get(Version, _uuid.UUID(body["version_id"]))
    assert minted is not None
    assert minted.status == "planned"
    # No build: no PipelineState for the minted version.
    assert (
        db_session.execute(select(PipelineState).where(PipelineState.version_id == minted.id)).scalar_one_or_none()
        is None
    )
    # The REQ exists and is linked.
    req = db_session.execute(
        select(BacklogItem).where(BacklogItem.project_id == project.id, BacklogItem.number == body["backlog_number"])
    ).scalar_one()
    assert req.version_id == minted.id

    # Idempotent per source message: a SECOND POST returns the SAME version, no new mint.
    versions_before = db_session.execute(
        select(func.count()).select_from(Version).where(Version.project_id == project.id)
    ).scalar_one()
    resp2 = client.post(f"/api/v1/pipeline/{consulted.id}/change-request", json={"message_id": str(msg.id)})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["version_id"] == body["version_id"]
    versions_after = db_session.execute(
        select(func.count()).select_from(Version).where(Version.project_id == project.id)
    ).scalar_one()
    assert versions_after == versions_before
