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
from tests.test_dedo_proposal import _build, _propose, _send
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


def test_dedos_brief_starts_a_fast_fix_only_with_the_work_kind(client, db_session, dedo_token, no_dispatch):  # noqa: F811
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()
    before = _numbers(db_session, project.id)
    brief = _navrhni(client, project.id).json()

    refused = _posli(client, user, project.id, brief["id"], work_kind=None)

    assert (refused.status_code, refused.json()["detail"]) == (400, fast_fix.WORK_KIND_REQUIRED)
    assert _numbers(db_session, project.id) == before and no_dispatch == [], "bez druhu práce vznikla oprava"
    waiting = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user)).json()
    assert waiting["id"] == brief["id"], "odmietnuté spustenie zadanie zavrelo — Manažér by ho už nespustil"

    started = _posli(client, user, project.id, brief["id"], work_kind="change")

    assert started.status_code == 200, started.text
    assert db_session.get(Version, uuid.UUID(started.json()["version_id"])).work_kind == "change"


def test_a_new_version_from_dedos_brief_needs_no_work_kind(client, db_session, dedo_token, no_dispatch):  # noqa: F811
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    db_session.commit()
    brief = _navrhni(client, project.id, action="new_version").json()

    created = _posli(client, user, project.id, brief["id"], work_kind=None)

    assert created.status_code == 200, created.text


def test_dedos_proposal_on_a_version_starts_a_fast_fix_only_with_the_work_kind(client, db_session, no_dispatch):  # noqa: F811
    user, version, _state = _build(db_session, version_number="0.1.0")
    proposal = _propose(db_session, version.id, content="Oprav vstupnú bránu.", action="fast_fix")

    refused = _send(client, user, version.id, proposal, text="Oprav vstupnú bránu.", work_kind=None)

    assert (refused.status_code, refused.json()["detail"]) == (400, fast_fix.WORK_KIND_REQUIRED)
    assert _numbers(db_session, version.project_id) == ["0.1.0"]

    started = _send(client, user, version.id, proposal, text="Oprav vstupnú bránu.", work_kind="fix")

    assert started.status_code == 200, started.text
    patch = db_session.execute(
        select(Version).where(Version.project_id == version.project_id, Version.version_number == "0.1.1")
    ).scalar_one()
    assert patch.work_kind == "fix"


def test_the_fast_fix_dialog_starts_nothing_without_the_work_kind(client, db_session, no_dispatch):  # noqa: F811
    user, version, _state = _build(db_session, version_number="0.1.0")

    refused = client.post(
        "/api/v1/pipeline/fast-fix",
        headers=_bearer(user),
        json={"project_id": str(version.project_id), "directive": "Oprav preklep."},
    )

    assert (refused.status_code, refused.json()["detail"]) == (400, fast_fix.WORK_KIND_REQUIRED)
    # The route rolls the refused request back (with this test's own rows), so the evidence is that no build was
    # scheduled — a started fast fix always schedules its first turn.
    assert no_dispatch == []


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

    assert calls, "stráž nenašla ani jedno založenie rýchlej opravy — hľadá zle"
    unchosen = [(where, kind) for where, kind in calls if kind is None or not kind.endswith(".work_kind")]
    assert unchosen == [], f"rýchla oprava vzniká bez voľby Manažéra: {unchosen}"
