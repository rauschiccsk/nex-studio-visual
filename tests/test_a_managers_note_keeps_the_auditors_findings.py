"""DEV-100 — the manager's note to a verification fix travels WITH the Auditor's findings, never instead of them.

12.–13.10.2026, NEX Inbox 1.8.0: the manager's note on the verification card said that points 3(a) and 3(b) of the
Auditor's findings are mandatory. The agent got that note alone as its fix brief — not the findings it named — and
stopped with a question. The brief was built from the newest verification message only, and the note replaced the
FAIL verdict it answers.

Director 13.10.2026 approved the batch with this ticket: the brief carries both, the note comes last and wins where
the two disagree. The one-click fix records the Auditor's own brief as the manager's message — that one is never
doubled, neither in new records nor in those written before this change.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _rec_fail_verdict,
    _rec_fix_critique,
    _seed_done_tasks,
    _seed_verifikacia,
    _stub_auditor,
    _stub_critique,
    _stub_smoke,
    _tasks,
    _verdict_fail,
)

FINDING_A = "3(a) Zoznam faktúr padne, keď dodávateľ nemá IČO."
FINDING_B = "3(b) Chýba skúška na ISDOC bez objednávkového kódu."
PROPOSED = "Oprav načítanie zoznamu faktúr a doplň skúšku ISDOC bez kódu."
NOTE = "Body 3(a) a 3(b) z nálezov Auditora sú povinné, nie len odporúčané."


async def _card(db_session, monkeypatch):
    """A verification FAIL that the critic did not vet — the card offers the one-click fix and the guided fix."""
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail([FINDING_A, FINDING_B], proposed_fix=PROPOSED))
    _stub_smoke(monkeypatch)
    _stub_critique(monkeypatch, verdict="reject", why="nevie sa to overiť")
    _ban_deploy_calls(monkeypatch)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)
    await orchestrator.run_dispatch(db_session, version.id)
    return version


async def _decide(db_session, version, **answer):
    return await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "verifikacia_fix_next", **answer},
    )


def _agents_brief(db_session, version) -> str:
    """What the agent gets: the description of the open fix task."""
    open_tasks = [t for t in _tasks(db_session, version.id) if t.status in ("todo", "in_progress")]
    assert len(open_tasks) == 1, [t.title for t in open_tasks]
    return open_tasks[0].description or ""


@pytest.mark.parametrize("option", ["guide", "fix_it"])
async def test_the_note_reaches_the_agent_with_the_findings_it_names(db_session, monkeypatch, option):
    version = await _card(db_session, monkeypatch)

    await _decide(db_session, version, option_id=option, note=NOTE)

    brief = _agents_brief(db_session, version)
    for part in (FINDING_A, FINDING_B, PROPOSED, NOTE):
        assert brief.count(part) == 1, f"{part!r} is not in the brief exactly once:\n{brief}"
    assert brief.index(FINDING_B) < brief.index(NOTE), "the note comes after what it answers"
    note_heading = brief[: brief.index(NOTE)].rstrip().splitlines()[-1]
    assert note_heading == "### Pokyn Manažéra — má prednosť, kde sa s nálezmi Auditora rozchádza"
    assert brief == orchestrator._latest_verifikacia_fix_scope(db_session, version.id)


async def test_the_one_click_fix_hands_the_findings_once(db_session, monkeypatch):
    version = await _card(db_session, monkeypatch)

    await _decide(db_session, version, option_id="fix_it")

    brief = _agents_brief(db_session, version)
    assert brief.count(FINDING_A) == 1 and brief.count(PROPOSED) == 1, brief
    assert "Pokyn Manažéra" not in brief, "the one click is not the manager's own words"


@pytest.mark.parametrize("vetted", [False, True], ids=["auditors-scope", "critics-scope"])
async def test_a_one_click_record_is_the_brief_as_it_stands(db_session, vetted):
    """The one-click fix records the Auditor's brief as the manager's message — also the one with the critic's
    vetted scope — and builds mid-fix keep such records from before this change. None of them is doubled."""
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _rec_fail_verdict(db_session, version.id, proposed_fix=PROPOSED, findings=[FINDING_A, FINDING_B])
    if vetted:
        _rec_fix_critique(db_session, version.id, verdict="reject", corrected_scope="NARROWED_SCOPE", why="zúž")
    auditors = orchestrator._latest_verifikacia_fix_scope(db_session, version.id)
    assert ("NARROWED_SCOPE" in auditors) is vetted
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=auditors,
        payload={"phase": "verifikacia", "manazer_fix_directive": True},
    )
    db_session.flush()

    assert orchestrator._latest_verifikacia_fix_scope(db_session, version.id) == auditors


async def test_the_critics_vetted_scope_travels_with_the_note(db_session):
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id, iteration=1)
    _rec_fail_verdict(db_session, version.id, proposed_fix="RAW_SCOPE", findings=[FINDING_A])
    _rec_fix_critique(db_session, version.id, verdict="narrow", corrected_scope="NARROWED_SCOPE", why="zúž")
    _note(db_session, version.id, NOTE)

    brief = orchestrator._latest_verifikacia_fix_scope(db_session, version.id)

    assert "NARROWED_SCOPE" in brief and "RAW_SCOPE" not in brief
    assert brief.index(FINDING_A) < brief.index(NOTE)


async def test_a_note_after_a_passed_verification_carries_no_findings(db_session):
    """An adjustment after a PASS answers no fault: the PASS's own notes are not faults (ICCINT-115 — the Auditor
    writes praise there too) and an older FAIL's findings are not dug up for it."""
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _rec_fail_verdict(db_session, version.id, proposed_fix=PROPOSED, findings=["staré nálezy"])
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia", "findings": ["AI Agent svoju prácu spravil správne."]},
    )
    _note(db_session, version.id, "Doplň chýbajúci negatívny test.")

    brief = orchestrator._latest_verifikacia_fix_scope(db_session, version.id)

    assert brief.endswith("Doplň chýbajúci negatívny test.")
    assert "staré nálezy" not in brief and PROPOSED not in brief and "spravil správne" not in brief


async def test_the_card_says_the_guidance_goes_along_with_the_findings(db_session, monkeypatch):
    version = await _card(db_session, monkeypatch)

    cards = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("consultation")]
    options = cards[-1].payload["consultation"]["decisions"][0]["options"]
    guide = next(o for o in options if o["id"] == "guide")

    assert "namiesto" not in guide["detail"]
    assert "spolu s nálezmi Auditora" in guide["detail"]


def _note(db, version_id, text: str) -> None:
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=text,
        payload={"phase": "verifikacia", "manazer_fix_directive": True},
    )
    db.flush()
