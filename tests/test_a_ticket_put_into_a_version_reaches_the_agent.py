"""DEV-455 (and DEV-134, DEV-361 before it) — the tickets put into a version reach the Staviteľ in Príprava, once.

Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety.“ The
Manažér puts a ticket of the project into a version on the Plane tikety tab; the opening brief of Príprava carries it,
or — when it came while Príprava already ran — the next round (an answer, a return), once. A version past Príprava,
a fast fix and a finished version take no ticket, and the sentence says why.
"""

from __future__ import annotations

import pytest

from backend.db.models.pipeline import PipelineState
from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version
from backend.services import orchestrator, version_tickets
from backend.services.pipeline_status import PipelineStatusBlock
from tests.test_orchestrator_v2_priprava_dial import _make_version, _seed_state

ANSWER = "Manažér odpovedal na tvoju otázku: Bod 3 — identifikátor záznamu je v tikete."
RETURN = "Manažér ťa vrátil na úpravu fázy 'priprava': doplň listy a výpisy."
TITLE = "Model rolí podľa schémy, listy a výpisy a identifikátory matričných záznamov"
TEXT = "Každý potvrdený záznam dostane identifikátor v tvare BRA1-K-12."
LINE = f"DEV-404: {TITLE} — {TEXT}"


@pytest.fixture
def world(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, project = _make_version(db_session)
    prompts: list[str] = []

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        prompts.append(prompt)
        return PipelineStatusBlock(stage=stage, kind="question", summary="?", question="Bod 4?", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return {"db": db_session, "version": version, "project": project, "prompts": prompts}


def _put_in(world, number=404, title=TITLE, text=TEXT, version=None) -> VersionTicket:
    link = VersionTicket(
        version_id=(version or world["version"]).id,
        plane_issue_id=f"issue-{number}",
        plane_identifier=f"DEV-{number}",
        title=title,
        text=text,
    )
    world["db"].add(link)
    world["db"].flush()
    return link


async def _round(world, directive=None) -> str:
    """One round of Príprava as the background runner starts it; the prompt the Staviteľ got."""
    state = world["db"].query(PipelineState).filter_by(version_id=world["version"].id).one()
    state.status = "agent_working"
    world["db"].flush()
    await orchestrator.run_dispatch(world["db"], world["version"].id, directive=directive)
    return world["prompts"][-1]


# ── the ticket reaches the Staviteľ ───────────────────────────────────────────────────────────────────────────────


async def test_a_ticket_put_in_during_priprava_rides_with_the_next_answer_once(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    opening = await _round(world)
    assert "DEV-404" not in opening

    link = _put_in(world)
    answered = await _round(world, ANSWER)
    later = await _round(world, "Manažér odpovedal na tvoju otázku: Bod 4 — áno.")

    assert answered.startswith(ANSWER)
    assert orchestrator.BACKLOG_HEADING in answered and LINE in answered
    assert "DEV-404" not in later  # handed over once
    assert link.delivered_at is not None


async def test_a_ticket_rides_also_with_a_return_to_priprava(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    await _round(world)
    _put_in(world)

    returned = await _round(world, RETURN)

    assert returned.startswith(RETURN) and LINE in returned


async def test_a_ticket_in_the_opening_brief_is_not_handed_again(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    _put_in(world)

    opening = await _round(world)
    answered = await _round(world, ANSWER)

    assert LINE in opening
    assert "Po nasadení verzie do PROD ich kokpit uzavrie" in opening
    assert "DEV-404" not in answered and orchestrator.BACKLOG_HEADING not in answered


async def test_only_the_new_ticket_rides_and_none_of_another_version(world):
    db, project = world["db"], world["project"]
    _seed_state(db, world["version"].id, stage="priprava", actor="ai_agent")
    _put_in(world, number=1, title="Prvý", text="už v úvodnom pokyne")
    await _round(world)
    _put_in(world)
    other = Version(project_id=project.id, version_number="9.9.9")
    db.add(other)
    db.flush()
    _put_in(world, number=5, title="Tiket inej verzie", version=other)

    answered = await _round(world, ANSWER)

    assert LINE in answered
    assert "DEV-1:" not in answered and "Tiket inej verzie" not in answered


async def test_a_round_that_never_reached_the_agent_hands_nothing_over(world, monkeypatch):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    await _round(world)
    link = _put_in(world)

    async def _broken(db, **_kw):
        raise RuntimeError("claude sa nespustil")

    with monkeypatch.context() as m:
        m.setattr(orchestrator, "invoke_agent_with_parse_retry", _broken)
        with pytest.raises(RuntimeError):
            await _round(world, ANSWER)
    assert link.delivered_at is None

    again = await _round(world, ANSWER)
    assert LINE in again


async def test_a_fast_fix_gets_no_tickets_of_a_version(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent", flow_type="fast_fix")
    link = _put_in(world)

    answered = await _round(world, ANSWER)

    assert "DEV-404" not in answered and link.delivered_at is None


# ── which versions take a ticket ──────────────────────────────────────────────────────────────────────────────────


def _version(db, project, number: str, *, status="active", stage=None, flow_type="new_version") -> Version:
    version = Version(project_id=project.id, version_number=number, status=status)
    db.add(version)
    db.flush()
    if stage is not None:
        _seed_state(db, version.id, stage=stage, actor="ai_agent", flow_type=flow_type)
    return version


def test_a_version_past_priprava_a_fast_fix_and_a_finished_one_are_refused_with_the_reason(world):
    db, project = world["db"], world["project"]
    cases = {
        _version(db, project, "2.0.0", stage="navrh"): (
            "Verzia 2.0.0 má schválenú Špecifikáciu — tiket by sa do nej už nedostal. Zaraď ho do ďalšej verzie."
        ),
        _version(db, project, "2.0.1", stage="priprava", flow_type="fast_fix"): (
            "Verzia 2.0.1 je rýchla oprava — nemá Špecifikáciu, do ktorej by sa tiket dostal. Zaraď ho do novej "
            "verzie alebo z neho spusti vlastnú rýchlu opravu."
        ),
        _version(db, project, "1.0.0", status="done"): "Verzia 1.0.0 je hotová — tiket by sa do nej už nedostal.",
    }
    for version, reason in cases.items():
        assert version_tickets.assign_refusal(db, version) == reason, version.version_number


def test_only_a_version_not_started_and_one_in_priprava_are_offered(world):
    db, project = world["db"], world["project"]
    planned = _version(db, project, "3.0.0", status="planned")
    preparing = _version(db, project, "3.1.0", stage="priprava")
    _version(db, project, "3.2.0", stage="programovanie")
    _version(db, project, "2.0.0", status="released")

    offered = [v.version_number for v in version_tickets.open_versions(db, project.id)]

    assert planned.version_number in offered and preparing.version_number in offered
    assert "3.2.0" not in offered and "2.0.0" not in offered
