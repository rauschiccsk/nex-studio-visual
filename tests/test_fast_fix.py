"""Tests for the Fast-Fix Lane service plumbing (F-009, CR-NS-094).

Covers the pure pieces the orchestrator does NOT own: the semver PATCH bump, the semver-max base
selection, PATCH-version creation, and the ONE-minimal-Task materialization (idempotent). The
orchestrator stage-routing / escalation / build-verify behavior is exercised in test_orchestrator.py.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import fast_fix


def _make_project(db_session, *, version_numbers=()):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    for vn in version_numbers:
        db_session.add(Version(project_id=project.id, version_number=vn))
    db_session.flush()
    return project, user


# ── semver bump ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version_number,expected",
    [
        ("0.6.0", "0.6.1"),
        ("0.9.0", "0.9.1"),
        ("0.6.9", "0.6.10"),
        ("v1.2.9", "v1.2.10"),
        ("10.0.0", "10.0.1"),
        ("1.2.3-rc1", "1.2.4"),  # pre-release suffix dropped from the bump
    ],
)
def test_bump_patch(version_number, expected):
    assert fast_fix.bump_patch(version_number) == expected


@pytest.mark.parametrize("bad", ["abc", "1.2", "v1", "", "1.x.0"])
def test_bump_patch_rejects_non_semver(bad):
    with pytest.raises(ValueError):
        fast_fix.bump_patch(bad)


# ── latest semver base ─────────────────────────────────────────────────────────


def test_latest_semver_version_picks_semver_max_not_lexicographic(db_session):
    # 0.10.0 > 0.9.0 by semver but '0.10.0' < '0.9.0' lexicographically — the bump must use semver order.
    project, _ = _make_project(db_session, version_numbers=["0.2.0", "0.9.0", "0.10.0"])
    latest = fast_fix.latest_semver_version(db_session, project.id)
    assert latest.version_number == "0.10.0"


def test_latest_semver_version_skips_non_semver(db_session):
    project, _ = _make_project(db_session, version_numbers=["0.1.0", "1.deadbeef.0"])
    latest = fast_fix.latest_semver_version(db_session, project.id)
    assert latest.version_number == "0.1.0"


def test_latest_semver_version_no_base_raises(db_session):
    project, _ = _make_project(db_session, version_numbers=["1.deadbeef.0"])
    with pytest.raises(ValueError):
        fast_fix.latest_semver_version(db_session, project.id)


# ── create patch version ───────────────────────────────────────────────────────


def test_create_patch_version_bumps_and_creates_planned(db_session):
    project, user = _make_project(db_session, version_numbers=["0.6.0"])
    version = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=user.id)
    assert version.version_number == "0.6.1"
    assert version.status == "planned"
    assert version.project_id == project.id
    # actually persisted
    persisted = db_session.execute(select(Version).where(Version.id == version.id)).scalar_one()
    assert persisted.version_number == "0.6.1"


def test_create_patch_version_anchors_on_semver_max(db_session):
    project, user = _make_project(db_session, version_numbers=["0.9.0", "0.10.0"])
    version = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=user.id)
    assert version.version_number == "0.10.1"


def test_create_patch_version_no_base_raises(db_session):
    project, user = _make_project(db_session, version_numbers=[])
    with pytest.raises(ValueError):
        fast_fix.create_patch_version(db_session, project_id=project.id, user_id=user.id)


# ── ensure_build_task ──────────────────────────────────────────────────────────


def _seed_version_with_kickoff(db_session, directive):
    project, _ = _make_project(db_session, version_numbers=["0.1.0"])
    version = Version(project_id=project.id, version_number="0.1.1")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="kickoff",
            author="director",
            recipient="coordinator",
            kind="kickoff",
            content="Spustenie pipeline.",
            payload={"flow_type": "fast_fix", "directive": directive},
        )
    )
    db_session.flush()
    return version


def test_ensure_build_task_materializes_from_directive(db_session):
    version = _seed_version_with_kickoff(db_session, "Oprav preklep v hlavičke faktúry\n(detail nižšie)")
    task = fast_fix.ensure_build_task(db_session, version.id)

    assert task.task_type == "backend" and task.status == "todo"
    assert task.description == "Oprav preklep v hlavičke faktúry\n(detail nižšie)"
    assert task.title == "Oprav preklep v hlavičke faktúry"  # first line, trimmed
    # exactly one Epic / Feat / Task under the version
    epics = db_session.execute(select(Epic).where(Epic.version_id == version.id)).scalars().all()
    assert len(epics) == 1 and epics[0].title == "Rýchla oprava"
    feats = db_session.execute(select(Feat).where(Feat.epic_id == epics[0].id)).scalars().all()
    assert len(feats) == 1
    tasks = db_session.execute(select(Task).where(Task.feat_id == feats[0].id)).scalars().all()
    assert len(tasks) == 1


def test_ensure_build_task_idempotent(db_session):
    version = _seed_version_with_kickoff(db_session, "Oprav VS sanitizáciu")
    first = fast_fix.ensure_build_task(db_session, version.id)
    second = fast_fix.ensure_build_task(db_session, version.id)
    assert first.id == second.id
    # still exactly one task under the version
    tasks = (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id)
        )
        .scalars()
        .all()
    )
    assert len(tasks) == 1


def test_ensure_build_task_missing_directive_falls_back(db_session):
    # No kickoff directive → a generic fast-fix task title (never crashes).
    project, _ = _make_project(db_session, version_numbers=["0.1.0"])
    version = Version(project_id=project.id, version_number="0.1.1")
    db_session.add(version)
    db_session.flush()
    task = fast_fix.ensure_build_task(db_session, version.id)
    assert task.title == "Rýchla oprava" and task.description == ""


def test_ensure_build_task_unknown_version_raises(db_session):
    with pytest.raises(ValueError):
        fast_fix.ensure_build_task(db_session, uuid.uuid4())


# ── kickoff_directive (public — read by the orchestrator to seed the kickoff brief, CR-NS-097) ──


def test_kickoff_directive_reads_payload(db_session):
    version = _seed_version_with_kickoff(db_session, "Premenuj 'Firmy' na 'Dodávatelia'")
    assert fast_fix.kickoff_directive(db_session, version.id) == "Premenuj 'Firmy' na 'Dodávatelia'"


def test_kickoff_directive_none_without_kickoff(db_session):
    project, _ = _make_project(db_session, version_numbers=["0.1.0"])
    version = Version(project_id=project.id, version_number="0.1.1")
    db_session.add(version)
    db_session.flush()
    assert fast_fix.kickoff_directive(db_session, version.id) is None
