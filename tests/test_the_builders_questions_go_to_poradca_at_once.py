"""DEV-483 — the fast path with Poradca: the Staviteľ's questions come at once, Poradca assesses them all, the Manažér
approves a summary; switchable per account.

Director 21.11.2026: „Chcel by som však vytvoriť nejakú rýchlejšiu cestu, kde Staviteľ zadal by tie otázky (všetky)
Poradcovi, Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu. … Chcem aby
táto možnosť bola nastaviteľná pre každého používateľa osobitne.“
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.db.models.batch_review import BatchReview
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.poradca import DONE, RUNNING, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.services import batch_review, orchestrator
from backend.services.orchestrator import OrchestratorError
from backend.services.pipeline_status import PipelineStatusBlock
from tests.test_orchestrator_v2_consultation import _seed_consultation

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

ANSWER = """Posúdil som všetky karty.

<posudok karta="1">
voľba: Voľba A1
prečo: Zodpovedá Špecifikácii §3.
rozhoduje: poradca
</posudok>

<posudok karta="2">
voľba: Voľba B2
prečo: A2 by rozbila export.
rozhoduje: poradca
</posudok>
"""


def _owner(db, version) -> User:
    project = db.get(Project, version.project_id)
    return db.get(User, project.created_by)


def _reviewed(db, version, *, content=ANSWER, status=DONE, failure=None) -> BatchReview:
    """Poradca got the cards and answered (or is answering, or never got them)."""
    _c, seq = orchestrator._latest_consultation(db, version.id)
    owner = _owner(db, version)
    conversation = PoradcaConversation(
        project_id=version.project_id,
        author_id=owner.id,
        version_id=version.id,
        title="t",
        claude_session_id=uuid.uuid4(),
    )
    db.add(conversation)
    db.flush()
    answer = PoradcaMessage(conversation_id=conversation.id, author="poradca", content=content, status=status)
    db.add(answer)
    db.flush()
    record = BatchReview(
        version_id=version.id,
        consultation_seq=seq,
        conversation_id=conversation.id,
        answer_message_id=None if failure else answer.id,
        failure=failure,
    )
    db.add(record)
    db.flush()
    return record


# ── Poradca's word per card ──────────────────────────────────────────────────────────────────────────────────────


def test_each_assessment_is_read_with_its_choice_why_and_who_decides():
    found = batch_review.parse(
        ANSWER
        + '<posudok karta="3">\nvoľba: „Vlastné rozhodnutie: dať oboje“\nprečo: x\nrozhoduje: manažér\n</posudok>'
        + '<posudok karta="4">\nprečo: bez voľby\n</posudok>'
    )

    assert found[1] == batch_review.Assessment("Voľba A1", "Zodpovedá Špecifikácii §3.", False)
    assert found[2].choice == "Voľba B2"
    assert found[3] == batch_review.Assessment("Vlastné rozhodnutie: dať oboje", "x", True)
    assert 4 not in found, "an assessment without a choice is none"


def test_the_question_carries_every_card_with_the_builders_recommendation_marked():
    text = batch_review.question(
        [{"question": "Otázka 1?", "options": [{"label": "A", "recommended": True}, {"label": "B", "detail": "d"}]}]
    )

    assert text.startswith("Posúď prosím naraz všetky karty rozhodnutí tejto konzultácie (1).")
    assert '<posudok karta="N">' in text
    assert "Karta 1: Otázka 1?\n- A (odporúča Staviteľ)\n- B — d" in text


# ── the summary ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_summary_says_agreement_disagreement_and_the_managers_own_decisions(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)

    found = batch_review.summary(db_session, version.id)

    assert found["status"] == "done"
    rows = {r["key"]: r for r in found["rows"]}
    assert (rows["d1"]["state"], rows["d1"]["option_id"], rows["d1"]["builder_label"]) == ("agree", "a", "Voľba A1")
    assert (rows["d2"]["state"], rows["d2"]["option_id"], rows["d2"]["poradca_label"]) == ("disagree", None, "Voľba B2")
    assert rows["d2"]["poradca_why"] == "A2 by rozbila export."
    assert found["counts"]["agree"] == 1 and found["counts"]["disagree"] == 1


def test_a_card_that_belongs_to_the_manager_is_never_an_agreement(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version, content=ANSWER.replace("rozhoduje: poradca", "rozhoduje: manažér", 1))

    rows = {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}

    assert rows["d1"]["state"] == "manager" and rows["d1"]["option_id"] is None


def test_while_poradca_answers_the_rows_wait_and_a_failure_is_said(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version, content="", status=RUNNING)
    assert {r["state"] for r in batch_review.summary(db_session, version.id)["rows"]} == {"waiting"}


def test_a_failure_is_said_and_no_row_waits(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version, failure=batch_review.BUSY)
    found = batch_review.summary(db_session, version.id)
    assert (found["status"], found["failure"]) == ("failed", batch_review.BUSY)
    assert {r["state"] for r in found["rows"]} == {"missing"}


def test_without_a_review_there_is_no_summary(db_session):
    assert batch_review.summary(db_session, _seed_consultation(db_session).id) is None


# ── approving in the summary ─────────────────────────────────────────────────────────────────────────────────────


async def test_an_agreement_is_approved_in_the_summary_and_the_history_says_who(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)
    owner = _owner(db_session, version)
    owner.first_name, owner.last_name = "Zoltán", "Rausch"

    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "d1", "option_id": "a", "via": batch_review.VIA},
        acting_user=owner,
    )

    answer = (
        db_session.query(PipelineMessage)
        .filter_by(version_id=version.id, kind="answer")
        .order_by(PipelineMessage.seq.desc())
        .first()
    )
    assert answer.content == (
        "Otázka 1? → Voľba A1 — schválené v súhrne: Staviteľ a Poradca sa zhodli (schválil Zoltán Rausch)"
    )
    assert answer.payload["consultation_decision"]["via"] == batch_review.VIA
    rows = {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}
    assert rows["d1"]["state"] == "decided"


async def test_a_disagreement_or_another_option_cannot_be_approved_in_the_summary(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)

    for payload in ({"decision_key": "d2", "option_id": "a"}, {"decision_key": "d1", "option_id": "b"}):
        with pytest.raises(OrchestratorError, match="nie je zhoda Staviteľa a Poradca"):
            await orchestrator.apply_action(
                db_session, version_id=version.id, action="decide", payload={**payload, "via": batch_review.VIA}
            )


# ── handing the cards to Poradca ─────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def poradca(monkeypatch):
    from backend.services.poradca import readiness, runner

    asked: list[tuple] = []

    def ask(db, conversation, question, user, files=()):
        answer = PoradcaMessage(conversation_id=conversation.id, author="poradca", content="", status=RUNNING)
        db.add(answer)
        db.flush()
        asked.append((conversation, question, user))
        return None, answer

    monkeypatch.setattr(runner, "ask", ask)
    monkeypatch.setattr(readiness, "status", lambda db: SimpleNamespace(ready=True, problems=[]))
    return asked


def test_the_cards_go_to_poradca_at_once_only_for_an_account_that_wants_it_and_once(db_session, poradca):
    version = _seed_consultation(db_session)
    assert batch_review.start_if_wanted(db_session, version.id) is None and poradca == []

    _owner(db_session, version).batch_review = True
    first = batch_review.start_if_wanted(db_session, version.id)
    again = batch_review.start_if_wanted(db_session, version.id)

    assert first is not None and again.id == first.id and len(poradca) == 1
    conversation, question, user = poradca[0]
    assert user.id == _owner(db_session, version).id and conversation.review_stage == "navrh"
    assert "Karta 2: Otázka 2?" in question
    assert batch_review.summary(db_session, version.id)["status"] == "running"


def test_when_poradca_cannot_run_the_reason_is_kept_and_the_cards_wait_one_by_one(db_session, poradca, monkeypatch):
    from backend.services.poradca import readiness

    monkeypatch.setattr(readiness, "status", lambda db: SimpleNamespace(ready=False, problems=["limit predplatného"]))
    version = _seed_consultation(db_session)
    _owner(db_session, version).batch_review = True

    record = batch_review.start_if_wanted(db_session, version.id)

    assert record.failure == batch_review.NOT_READY.format(why="limit predplatného") and poradca == []
    assert batch_review.summary(db_session, version.id)["status"] == "failed"


def test_a_build_not_on_cards_hands_nothing(db_session, poradca):
    version = _seed_consultation(db_session, status="awaiting_manazer")
    _owner(db_session, version).batch_review = True
    assert batch_review.start_if_wanted(db_session, version.id) is None and poradca == []


# ── Príprava asks at once ────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def priprava(db_session, monkeypatch, tmp_path):
    from tests.test_orchestrator_v2_priprava_dial import _make_version, _seed_state

    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    prompts: list[str] = []
    cards = {
        "id": "q1",
        "decisions": [
            {
                "key": "k1",
                "question": "Jazyk?",
                "origin": "objav",
                "options": [
                    {"id": "sk", "label": "Slovenčina", "recommended": True},
                    {"id": "hu", "label": "Maďarčina"},
                ],
            }
        ],
    }

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        prompts.append(prompt)
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="ai_agent",
            recipient="manazer",
            kind="consultation",
            content="otázky",
            payload={"consultation": cards},
        )
        return PipelineStatusBlock.model_validate(
            {"stage": stage, "kind": "consultation", "summary": "otázky", "awaiting": "manazer", "consultation": cards}
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return db_session, version, project, prompts


async def _round(db, version):
    state = db.query(PipelineState).filter_by(version_id=version.id).one()
    state.status = "agent_working"
    db.flush()
    return await orchestrator.run_dispatch(db, version.id)


async def test_an_account_with_the_fast_path_gets_the_questions_of_priprava_at_once_as_cards(priprava):
    db, version, project, prompts = priprava
    db.get(User, project.created_by).batch_review = True

    state = await _round(db, version)

    assert orchestrator.QUESTIONS_AT_ONCE in prompts[-1]
    assert (state.status, state.block_reason) == ("blocked", "decision_needed")
    assert state.next_action == "Manažér: rozhodni 1/1 (1 otázka Prípravy naraz)."
    consultation, _seq = orchestrator._latest_consultation(db, version.id)
    assert consultation["source"] == "priprava"


async def test_without_the_fast_path_priprava_asks_as_before(priprava):
    db, version, _project, prompts = priprava

    await _round(db, version)

    assert "OTÁZKY NARAZ" not in prompts[-1]


# ── the switch per account ───────────────────────────────────────────────────────────────────────────────────────


def _client(db, user) -> TestClient:
    from backend.api.routes.users import router
    from backend.core.security import get_current_user
    from backend.db.session import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/users")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _user(db, role: str) -> User:
    user = User(username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.x", password_hash="x", role=role)
    db.add(user)
    db.flush()
    return user


def test_the_admin_switches_the_fast_path_per_account_and_it_starts_off(db_session):
    admin, learner = _user(db_session, "ri"), _user(db_session, "shu")
    client = _client(db_session, admin)

    listed = {a["username"]: a["on"] for a in client.get("/api/v1/users/batch-review").json()}
    assert listed[learner.username] is False

    r = client.put(f"/api/v1/users/{admin.id}/batch-review", json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True
    assert admin.batch_review is True and learner.batch_review is False


def test_a_learner_cannot_switch_it(db_session):
    learner = _user(db_session, "shu")
    r = _client(db_session, learner).put(f"/api/v1/users/{learner.id}/batch-review", json={"on": True})
    assert r.status_code == 403 and learner.batch_review is False


def test_both_charters_know_the_fast_path():
    builder = " ".join((TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8").split())
    poradca = " ".join((TEMPLATES / "poradca-charter.md").read_text(encoding="utf-8").split())

    assert "keď ti kokpit v pokyne napíše **OTÁZKY NARAZ**" in builder
    assert '`<posudok karta="N">`' in poradca and "`rozhoduje:`" in poradca
