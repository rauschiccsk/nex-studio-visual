"""DEV-110 — a fast fix takes its number from the last finished version, not from one still being built.

13.10.2026, NEX Manager: 1.3.0 is being built (preview stage), 1.2.2 runs on PROD. A fast fix of a PROD error would
have been numbered 1.3.1 — the highest version of any state — instead of 1.2.3; and 1.3.0, once out, would have found
its first fix number already taken. A version is finished when its build ended or it was released — the one rule the
delivered code statement already used, now in one place.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.services import fast_fix
from backend.services import version as version_service
from tests.test_fast_fix import _make_project


def _version(db, project, number: str, *, status: str = "planned", stage: str | None = None) -> Version:
    v = Version(project_id=project.id, version_number=number, status=status)
    db.add(v)
    db.flush()
    if stage is not None:
        db.add(
            PipelineState(
                version_id=v.id,
                flow_type="new_version",
                current_stage=stage,
                current_actor="ai_agent",
                status="done" if stage == "done" else "agent_working",
            )
        )
        db.flush()
    return v


def _fast_fix(db, project, user) -> Version:
    return fast_fix.create_patch_version(db, project_id=project.id, user_id=user.id, work_kind="fix")


def test_a_fix_of_what_runs_on_prod_is_numbered_from_it_not_from_the_build(db_session):
    project, user = _make_project(db_session)
    _version(db_session, project, "1.2.1", status="done", stage="done")
    _version(db_session, project, "1.2.2", status="released", stage="done")
    _version(db_session, project, "1.3.0", status="active", stage="vizual")

    assert _fast_fix(db_session, project, user).version_number == "1.2.3"


def test_a_build_that_ended_is_finished_before_it_is_released(db_session):
    """NEX Inbox: 1.7.1 built and delivered (its build ended), 1.8.0 still in verification."""
    project, user = _make_project(db_session)
    _version(db_session, project, "1.7.1", status="done", stage="done")
    _version(db_session, project, "1.8.0", status="active", stage="verifikacia")

    assert _fast_fix(db_session, project, user).version_number == "1.7.2"


def test_a_project_with_nothing_finished_is_told_so(db_session):
    project, user = _make_project(db_session)
    _version(db_session, project, "0.1.0", status="active", stage="programovanie")

    with pytest.raises(ValueError, match="nemá hotovú verziu"):
        _fast_fix(db_session, project, user)
    numbers = db_session.execute(select(Version.version_number).where(Version.project_id == project.id)).scalars()
    assert list(numbers) == ["0.1.0"]


def test_a_number_already_held_names_the_version_that_holds_it(db_session):
    """An earlier fast fix of 1.2.2 that never finished still holds 1.2.3 — say so, create nothing."""
    project, user = _make_project(db_session)
    _version(db_session, project, "1.2.2", status="released", stage="done")
    _version(db_session, project, "1.2.3", status="active", stage="programovanie")

    with pytest.raises(ValueError) as raised:
        _fast_fix(db_session, project, user)

    said = str(raised.value)
    assert "1.2.3" in said and "rozrobená" in said
    numbers = db_session.execute(select(Version.version_number).where(Version.project_id == project.id)).scalars()
    assert sorted(numbers) == ["1.2.2", "1.2.3"]


def test_finished_means_one_thing_everywhere(db_session):
    """The fast fix and the delivered code statement ask the same question in the same place."""
    project, _ = _make_project(db_session)
    cases = {
        "built": _version(db_session, project, "1.0.0", status="done", stage="done"),
        "released-without-a-build": _version(db_session, project, "0.9.0", status="released"),
        "building": _version(db_session, project, "1.1.0", status="active", stage="programovanie"),
        "planned": _version(db_session, project, "1.2.0"),
    }

    finished = {name for name, v in cases.items() if version_service.build_finished(db_session, v)}

    assert finished == {"built", "released-without-a-build"}
