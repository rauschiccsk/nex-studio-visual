"""DEV-448 — a change of the Pravidlá agenta reaches the agent whose session is already running.

20.11.2026: the cockpit 4.160.0 added the rule on working copies in ``private/`` (DEV-443) to the Staviteľ's charter;
the Staviteľ of Orthodox Hermenia 0.2.0 never got it — its ``private/kitsrc/`` grew from 190 to 281 files. A charter
reached the agent only when its session started, and one session serves a (project, role) across versions. The session
now keeps the fingerprint of the charter it last received; a resumed turn after a change gets the current text once.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.orchestrator import OrchestratorSession
from backend.services import claude_agent, orchestrator

CHARTER_V1 = "# Pravidlá agenta\n\n- staré pravidlo\n"
CHARTER_V2 = "# Pravidlá agenta\n\n- staré pravidlo\n- pracovné kópie do private/agent-work/<účel>/\n"


@pytest.fixture
def project(tmp_path, monkeypatch, db_session):
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    refreshed: list[str] = []
    monkeypatch.setattr(claude_agent, "_refresh_rules", lambda slug: refreshed.append(slug))
    slug = f"p-{uuid.uuid4().hex[:8]}"
    charter = orchestrator._agent_charter_path(slug, orchestrator.AI_AGENT_ROLE)
    charter.parent.mkdir(parents=True)
    charter.write_text(CHARTER_V1, encoding="utf-8")
    return slug, charter, refreshed


def _turn(db, slug):
    _session, is_first = orchestrator._resolve_orch_session(db, slug, orchestrator.AI_AGENT_ROLE)
    return orchestrator._charter_for_turn(db, slug, orchestrator.AI_AGENT_ROLE, is_first)


def test_a_new_session_gets_the_charter_as_ever_and_its_fingerprint_is_kept(db_session, project):
    slug, _charter, _refreshed = project

    assert _turn(db_session, slug) == ""
    row = db_session.query(OrchestratorSession).filter_by(project_slug=slug).one()
    assert row.charter_sha256 and len(row.charter_sha256) == 64


def test_a_resumed_turn_with_the_same_charter_adds_nothing(db_session, project):
    slug, _charter, refreshed = project
    _turn(db_session, slug)

    assert _turn(db_session, slug) == ""
    assert refreshed == [slug], "a resumed turn brings the rules up to the templates first"


def test_a_changed_charter_is_given_once_whole_and_said_to_replace_the_old(db_session, project):
    slug, charter, _refreshed = project
    _turn(db_session, slug)
    charter.write_text(CHARTER_V2, encoding="utf-8")

    added = _turn(db_session, slug)

    assert added.startswith(orchestrator.CHARTER_CHANGED_HEADING)
    assert "pracovné kópie do private/agent-work/<účel>/" in added
    assert _turn(db_session, slug) == "", "once per change, not at every turn"


def test_a_session_from_before_the_fingerprint_gets_the_current_charter_once(db_session, project):
    slug, _charter, _refreshed = project
    db_session.add(
        OrchestratorSession(project_slug=slug, role=orchestrator.AI_AGENT_ROLE, claude_session_id=uuid.uuid4())
    )
    db_session.flush()

    assert "staré pravidlo" in _turn(db_session, slug)
    assert _turn(db_session, slug) == ""


def test_a_charter_that_cannot_be_read_adds_nothing(db_session, project):
    slug, charter, _refreshed = project
    _turn(db_session, slug)
    charter.unlink()

    assert _turn(db_session, slug) == ""


async def test_the_build_turn_carries_the_current_charter_before_its_prompt(db_session, monkeypatch):
    """The wiring: every warm-session dispatch puts the added text before its own prompt."""
    from tests.test_orchestrator_v2_invoke_agent import _fake_claude, _make_version, _navrh_block

    fake = _fake_claude(monkeypatch)
    fake.response = _navrh_block()
    version, _project = _make_version(db_session)
    monkeypatch.setattr(orchestrator, "_charter_for_turn", lambda *_a: "AKTUÁLNE PRAVIDLÁ\n\n")

    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )

    assert fake.calls[-1]["prompt"].startswith("AKTUÁLNE PRAVIDLÁ\n\ngo")
