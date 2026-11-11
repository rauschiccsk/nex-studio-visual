"""DEV-361 — a requirement included in a version whose Príprava already runs reaches the Staviteľ with the next round.

Director 11.11.2026 (Dominik's addition to orthodox-register in Nápady, 0.1.0 in Príprava since 03.11.2026):
„Potrebujem overiť, že tent vklad do projektu dostane sa tam v poriadku, prekontroluj.“ It would not have: the Staviteľ
got the Zásobník's requirements only in the opening brief of Príprava (DEV-134), and every later round of Príprava is
the Manažér's answer, question or return — none carried them. The Zásobník offered the running version, the screen
said „zaradené do 0.1.0“, and at release the requirement would have been „realized“ without anyone building it. On the
finding and the proposal — „Súhlasím“.

Now a requirement rides with the next round of Príprava, once; a version past Príprava (approved Špecifikácia), a fast
fix and a finished version are not offered, and the sentence says why.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.backlog import router as backlog_router
from backend.core.security import get_current_user
from backend.db.models.backlog import BacklogItem
from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import backlog as backlog_service
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock
from tests.test_orchestrator_v2_priprava_dial import _make_version, _seed_state

ANSWER = "Manažér odpovedal na tvoju otázku: Bod 3 — identifikátor záznamu je v požiadavke zo Zásobníka."
RETURN = "Manažér ťa vrátil na úpravu fázy 'priprava': doplň listy a výpisy."
TITLE = "Model rolí podľa schémy, listy a výpisy a identifikátory matričných záznamov"
DESCRIPTION = "Každý potvrdený záznam dostane identifikátor v tvare BRA1-K-12."
LINE = f"REQ-4: {TITLE} — {DESCRIPTION}"


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


def _requirement(world, number=4, title=TITLE, description=DESCRIPTION) -> BacklogItem:
    item = BacklogItem(project_id=world["project"].id, number=number, title=title, description=description)
    world["db"].add(item)
    world["db"].flush()
    return item


def _include(world, item) -> None:
    backlog_service.assign_to_version(world["db"], item.id, world["version"].id)
    world["db"].flush()


async def _round(world, directive=None) -> str:
    """One round of Príprava as the background runner starts it; the prompt the Staviteľ got."""
    state = world["db"].query(PipelineState).filter_by(version_id=world["version"].id).one()
    state.status = "agent_working"
    world["db"].flush()
    await orchestrator.run_dispatch(world["db"], world["version"].id, directive=directive)
    return world["prompts"][-1]


# ── the requirement reaches the Staviteľ ────────────────────────────────────────────────────────────────────────────


async def test_a_requirement_included_during_priprava_rides_with_the_next_answer_once(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    opening = await _round(world)
    assert "REQ-4" not in opening

    _include(world, _requirement(world))
    answered = await _round(world, ANSWER)
    later = await _round(world, "Manažér odpovedal na tvoju otázku: Bod 4 — áno.")

    assert answered.startswith(ANSWER)
    assert orchestrator.BACKLOG_HEADING in answered and LINE in answered
    assert "REQ-4" not in later  # handed over once
    assert world["db"].query(BacklogItem).filter_by(number=4).one().delivered_at is not None


async def test_a_requirement_rides_also_with_a_return_to_priprava(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    await _round(world)
    _include(world, _requirement(world))

    returned = await _round(world, RETURN)

    assert returned.startswith(RETURN) and LINE in returned


async def test_a_requirement_in_the_opening_brief_is_not_handed_again(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    _include(world, _requirement(world))

    opening = await _round(world)
    answered = await _round(world, ANSWER)

    assert LINE in opening
    assert "REQ-4" not in answered and orchestrator.BACKLOG_HEADING not in answered


async def test_only_the_new_requirement_rides_and_none_other(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    _include(world, _requirement(world, number=1, title="Prvá", description="už v úvodnom pokyne"))
    await _round(world)
    _include(world, _requirement(world))
    _requirement(world, number=5, title="Nezaradená požiadavka")

    answered = await _round(world, ANSWER)

    assert LINE in answered
    assert "REQ-1" not in answered and "Nezaradená požiadavka" not in answered


async def test_a_round_that_never_reached_the_agent_hands_nothing_over(world, monkeypatch):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent")
    await _round(world)
    item = _requirement(world)
    _include(world, item)

    async def _broken(db, **_kw):
        raise RuntimeError("claude sa nespustil")

    with monkeypatch.context() as m:
        m.setattr(orchestrator, "invoke_agent_with_parse_retry", _broken)
        with pytest.raises(RuntimeError):
            await _round(world, ANSWER)
    assert item.delivered_at is None

    again = await _round(world, ANSWER)
    assert LINE in again


async def test_a_fast_fix_gets_no_requirements(world):
    _seed_state(world["db"], world["version"].id, stage="priprava", actor="ai_agent", flow_type="fast_fix")
    item = _requirement(world)
    item.status, item.version_id = "included", world["version"].id
    world["db"].flush()

    answered = await _round(world, ANSWER)

    assert "REQ-4" not in answered and item.delivered_at is None


# ── where a requirement may be included ─────────────────────────────────────────────────────────────────────────────


def _version(db, project, number: str, *, status="active", stage=None, flow_type="new_version") -> Version:
    version = Version(project_id=project.id, version_number=number, status=status)
    db.add(version)
    db.flush()
    if stage is not None:
        _seed_state(db, version.id, stage=stage, actor="ai_agent", flow_type=flow_type)
    return version


def test_a_version_past_priprava_a_fast_fix_and_a_finished_one_are_refused_with_the_reason(world):
    db, project = world["db"], world["project"]
    item = _requirement(world)
    cases = {
        _version(db, project, "2.0.0", stage="navrh"): (
            "Verzia 2.0.0 má schválenú Špecifikáciu — požiadavka by sa do nej už nedostala. Zaraď ju do ďalšej verzie."
        ),
        _version(db, project, "2.0.1", stage="priprava", flow_type="fast_fix"): (
            "Verzia 2.0.1 je rýchla oprava — nemá Špecifikáciu, do ktorej by sa požiadavka dostala. Zaraď ju do novej "
            "verzie."
        ),
        _version(db, project, "1.0.0", status="done"): "Verzia 1.0.0 je hotová — požiadavka by sa do nej už nedostala.",
    }
    for version, reason in cases.items():
        with pytest.raises(ValueError) as refused:
            backlog_service.assign_to_version(db, item.id, version.id)
        assert str(refused.value) == reason, version.version_number
        assert item.status == "open" and item.version_id is None


def test_a_version_not_started_and_one_in_priprava_are_offered(world):
    db, project = world["db"], world["project"]
    offered = (_version(db, project, "3.0.0", status="planned"), _version(db, project, "3.1.0", stage="priprava"))
    for number, version in enumerate(offered, start=11):
        item = _requirement(world, number=number, title=f"pre {version.version_number}")
        backlog_service.assign_to_version(db, item.id, version.id)
        assert (item.status, item.version_id) == ("included", version.id)


def test_moving_a_requirement_to_another_version_hands_it_over_again(world):
    db, project = world["db"], world["project"]
    item = _requirement(world)
    _include(world, item)
    item.delivered_at = datetime.now(timezone.utc)
    other = _version(db, project, "3.2.0", stage="priprava")

    backlog_service.assign_to_version(db, item.id, other.id)

    assert item.delivered_at is None


def _client(db, user) -> TestClient:
    app = FastAPI()
    app.include_router(backlog_router, prefix="/api/v1/backlog")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _owner(db, project):
    from backend.db.models.foundation import User

    return db.get(User, project.created_by)


def test_the_zasobnik_lists_each_version_with_why_it_is_not_offered(world):
    db, project = world["db"], world["project"]
    _seed_state(db, world["version"].id, stage="priprava", actor="ai_agent")
    _version(db, project, "2.0.0", stage="vizual")

    rows = _client(db, _owner(db, project)).get(f"/api/v1/backlog/versions?project_id={project.id}")

    assert rows.status_code == 200, rows.text
    by_number = {row["version_number"]: row["refusal"] for row in rows.json()}
    assert by_number[world["version"].version_number] is None
    assert by_number["2.0.0"].startswith("Verzia 2.0.0 má schválenú Špecifikáciu")


def test_the_route_refuses_a_version_past_priprava_with_the_sentence(world):
    db, project = world["db"], world["project"]
    item = _requirement(world)
    past = _version(db, project, "2.0.0", stage="programovanie")

    r = _client(db, _owner(db, project)).patch(f"/api/v1/backlog/{item.id}", json={"version_id": str(past.id)})

    assert r.status_code == 422
    assert r.json()["detail"].startswith("Verzia 2.0.0 má schválenú Špecifikáciu")


def test_the_versions_of_a_project_another_manager_leads_are_not_listed(world, db_session):
    from tests.test_backlog import _make_user

    stranger = _make_user(db_session, "shu")
    r = _client(db_session, stranger).get(f"/api/v1/backlog/versions?project_id={world['project'].id}")
    assert r.status_code in (403, 404)
