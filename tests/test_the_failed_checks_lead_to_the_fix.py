"""DEV-57 — when the project's checks failed, the deploy screen leads the manager to the fix, not back to the version.

10.10.2026, NEX Inbox 1.7.0: the UAT page said „Nasadenie je zastavené — kontroly projektu zlyhali … oprav ju rýchlou
opravou alebo novou verziou“ but its one button was „Otvoriť verziu“ — the finished version, not the fix; a brief for
the fix was waiting on the project and the notice did not say so. The Director had to ask how to go on.

The block now says where the fix is: the version already begun after the blocked one (a fast fix or a new version),
else Dedo's brief waiting on the project — and with neither the screen offers to start a fast fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.services import ci_status, dedo_project_proposal
from backend.services import deploy as deploy_service
from tests.test_red_ci_after_the_gate_stops_the_deploy import _as_owner, _ci, _finished_version

BLOCKED_AT = datetime(2026, 10, 8, 8, 58, tzinfo=timezone.utc)


def _add_version(db, project, number, *, created_at, stage):
    """A version of the project created at ``created_at`` (in one test transaction ``now()`` is one moment for every
    row — the real versions are created in separate transactions, minutes or days apart)."""
    v = Version(project_id=project.id, version_number=number, name=number, status="active", created_at=created_at)
    db.add(v)
    db.flush()
    if stage is not None:
        db.add(
            PipelineState(
                version_id=v.id,
                flow_type="fast_fix",
                current_stage=stage,
                current_actor="ai_agent",
                status="agent_working",
                next_action="",
            )
        )
        db.flush()
    return v


def _blocked(db):
    project, version = _finished_version(db)
    version.created_at = BLOCKED_AT
    db.flush()
    return project, version


def _fix_fields(matrix) -> tuple:
    block = matrix["deployability"]
    return block["next_version_id"], block["next_version_number"], block["dedo_brief"]


def test_with_no_fix_begun_and_no_brief_the_screen_is_to_offer_starting_one(db_session):
    project, _version = _blocked(db_session)
    # An older version left unfinished is not the fix of a newer one.
    _add_version(db_session, project, "1.6.9", created_at=BLOCKED_AT - timedelta(days=2), stage="programovanie")

    matrix = deploy_service.build_matrix(db_session, project, ci=_ci("red"))

    assert matrix["deployability"]["cause"] == "ci_red"
    assert _fix_fields(matrix) == (None, None, None)


def test_a_fix_already_begun_is_where_the_screen_leads(db_session):
    project, _version = _blocked(db_session)
    _add_version(db_session, project, "1.7.1", created_at=BLOCKED_AT + timedelta(days=2), stage="programovanie")
    fix = _add_version(db_session, project, "1.7.2", created_at=BLOCKED_AT + timedelta(days=2, minutes=5), stage=None)

    matrix = deploy_service.build_matrix(db_session, project, ci=_ci("red"))

    assert _fix_fields(matrix) == (fix.id, "1.7.2", None), "vedie k najnovšej rozpracovanej verzii"


def test_a_finished_newer_version_is_not_a_fix_in_progress(db_session):
    project, _version = _blocked(db_session)
    _add_version(db_session, project, "1.7.1", created_at=BLOCKED_AT + timedelta(days=1), stage="done")

    matrix = deploy_service.build_matrix(db_session, project, ci=_ci("red"))

    assert matrix["deployability"]["next_version_id"] is None


def test_dedos_brief_waiting_on_the_project_is_named(db_session):
    project, _version = _blocked(db_session)
    dedo_project_proposal.record(
        db_session, project_id=project.id, content="Fix the unstable gate check.", proposed_action="fast_fix"
    )
    db_session.flush()

    matrix = deploy_service.build_matrix(db_session, project, ci=_ci("red"))

    assert _fix_fields(matrix) == (None, None, "fast_fix")


def test_running_checks_say_nothing_about_a_fix(db_session):
    project, _version = _blocked(db_session)
    _add_version(db_session, project, "1.7.1", created_at=BLOCKED_AT + timedelta(days=1), stage="programovanie")
    running = _ci("unknown", bezi=True, detail="CI ešte beží (postup Release smoke gate, beh 38042619283)")

    matrix = deploy_service.build_matrix(db_session, project, ci=running)

    assert matrix["deployability"]["cause"] == "ci_running"
    assert "next_version_id" not in matrix["deployability"], "kontroly ešte bežia — o oprave nie je reč"


def test_the_deploy_screen_gets_where_the_fix_is(client, db_session, monkeypatch):
    project, _version = _blocked(db_session)
    fix = _add_version(db_session, project, "1.7.1", created_at=BLOCKED_AT + timedelta(days=2), stage="programovanie")
    dedo_project_proposal.record(
        db_session, project_id=project.id, content="Fix the unstable gate check.", proposed_action="fast_fix"
    )
    db_session.flush()
    _as_owner(db_session, project)

    async def _red(project_root, sha=None, *, cerstvy=False):
        return _ci("red")

    monkeypatch.setattr(ci_status, "stav_commitu", _red)

    block = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()["deployability"]

    assert (block["cause"], block["next_version_id"], block["next_version_number"], block["dedo_brief"]) == (
        "ci_red",
        str(fix.id),
        "1.7.1",
        "fast_fix",
    )
