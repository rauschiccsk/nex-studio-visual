"""DEV-56 — every way a fast fix starts carries the Manažér's work kind; without it the fast fix does not start.

The delivered-token statement (DEV-50) bills by the work kind: a fix of an error in delivered code is never billed,
a change is. DEV-50 put the choice into the „Rýchla oprava“ dialog — but a fast fix is also started from Dedo's
brief on the project and from Dedo's proposal on a version, and those started it undecided (10.10.2026, NEX Inbox
1.7.1). The choice now lives where a fast fix is created (``fast_fix.create_patch_version``), so no way can skip it.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

from sqlalchemy import select

from backend.db.models.versions import Version
from backend.services import fast_fix
from tests.test_dedo_proposal_before_the_work_starts import (  # noqa: F401 — fixtures
    _bearer,
    _make_project,
    _make_user,
    _navrhni,
    _posli,
    dedo_token,
    no_dispatch,
)

REPO = Path(__file__).resolve().parents[1]


def _numbers(db, project_id) -> list[str]:
    return sorted(db.execute(select(Version.version_number).where(Version.project_id == project_id)).scalars().all())


def _found(client, user, project, **body):
    return client.post(f"/api/v1/projects/{project.id}/fast-fixes", headers=_bearer(user), json=body)


def test_a_fast_fix_is_founded_only_with_the_work_kind(client, db_session):
    """DEV-492: the work kind is chosen when the fast fix is founded („Nová verzia“ → „Rýchla oprava“)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()
    before = _numbers(db_session, project.id)

    refused = _found(client, user, project)

    assert (refused.status_code, refused.json()["detail"]) == (400, fast_fix.WORK_KIND_REQUIRED)
    assert _numbers(db_session, project.id) == before, "a fix was founded without a work kind"

    founded = _found(client, user, project, work_kind="change")

    assert founded.status_code == 201, founded.text
    assert db_session.get(Version, uuid.UUID(founded.json()["id"])).work_kind == "change"


def test_a_fast_fix_may_be_a_gift(client, db_session):
    """DEV-118 — Director 14.10.2026: „GIFT - Dar“; a whole fast fix may be an attention of ICC, not only a task."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()

    founded = _found(client, user, project, work_kind="gift")

    assert founded.status_code == 201, founded.text
    assert db_session.get(Version, uuid.UUID(founded.json()["id"])).work_kind == "gift"


def test_a_new_version_from_dedos_brief_needs_no_work_kind(client, db_session, dedo_token, no_dispatch):  # noqa: F811
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()
    brief = _navrhni(client, project.id, action="new_version").json()

    created = _posli(client, user, project.id, brief["id"], work_kind=None)

    assert created.status_code == 200, created.text


def test_every_place_that_creates_a_fast_fix_passes_the_managers_work_kind():
    """The rule, not a list: every call of ``create_patch_version`` in the application passes ``work_kind`` taken
    from what the Manažér sent (``<request>.work_kind``) — never a constant, never left out."""
    calls = []
    for path in sorted((REPO / "backend").rglob("*.py")):
        if "tests" in path.relative_to(REPO).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name == "create_patch_version":
                kind = {k.arg: k.value for k in node.keywords}.get("work_kind")
                calls.append((f"{path.relative_to(REPO)}:{node.lineno}", ast.unparse(kind) if kind else None))

    assert calls, "the guard found no fast-fix creation at all — it looks in the wrong place"
    unchosen = [(where, kind) for where, kind in calls if kind is None or not kind.endswith(".work_kind")]
    assert unchosen == [], f"a fast fix is created without the Manažér's choice: {unchosen}"
