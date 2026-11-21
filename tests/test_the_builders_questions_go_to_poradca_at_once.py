"""DEV-483 — the fast path with Poradca: the Staviteľ's questions come at once, Poradca assesses them all, the Manažér
approves a summary; switchable per account.

Director 21.11.2026: „Chcel by som však vytvoriť nejakú rýchlejšiu cestu, kde Staviteľ zadal by tie otázky (všetky)
Poradcovi, Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu. … Chcem aby
táto možnosť bola nastaviteľná pre každého používateľa osobitne.“

DEV-486 — Director 21.11.2026: „Rýchla cesta má byť všetky otázky overené poradcom. … Ja to prečítam a ak všetko mi
vyhovuje s jednám tlačítkom schválim všetky otázky. … pri overení Poradca pri cca 80% otázok mal aj nejaké dodatočné
informácie pre Staviteľa, tu som to nevidel.“ Poradca gives each card his instruction for the Staviteľ, and the
summary approves his proposal on every card — the card he proposes differently too.
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
pokyn: —
</posudok>

<posudok karta="2">
voľba: Voľba B2
prečo: A2 by rozbila export.
pokyn: Pri exporte over aj dobropis so zápornou sumou.
Pridaj naň skúšku.
</posudok>
"""
INSTRUCTION_2 = "Pri exporte over aj dobropis so zápornou sumou.\nPridaj naň skúšku."


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


def test_each_assessment_is_read_with_its_choice_why_and_instruction_for_the_staviter():
    found = batch_review.parse(
        ANSWER
        + '<posudok karta="3">\nvoľba: „Vlastné rozhodnutie: dať oboje“\nprečo: x\nrozhoduje: manažér\n</posudok>'
        + '<posudok karta="4">\nprečo: bez voľby\n</posudok>'
    )

    assert found[1] == batch_review.Assessment("Voľba A1", "Zodpovedá Špecifikácii §3.", None), "„—“ is no instruction"
    assert found[2] == batch_review.Assessment("Voľba B2", "A2 by rozbila export.", INSTRUCTION_2)
    # an answer from before DEV-486 says who decides — read without it, and it never runs into „prečo“
    assert found[3] == batch_review.Assessment("Vlastné rozhodnutie: dať oboje", "x", None)
    assert 4 not in found, "an assessment without a choice is none"


def test_the_question_carries_every_card_with_the_builders_recommendation_marked():
    text = batch_review.question(
        [{"question": "Otázka 1?", "options": [{"label": "A", "recommended": True}, {"label": "B", "detail": "d"}]}]
    )

    assert text.startswith("Posúď prosím naraz všetky karty rozhodnutí tejto konzultácie (1) — každú tak dôkladne")
    assert '<posudok karta="N">' in text
    assert "\npokyn: čo má Staviteľ pri tejto voľbe ešte zapracovať" in text and "rozhoduje" not in text
    assert "Karta 1: Otázka 1?\n- A (odporúča Staviteľ)\n- B — d" in text


# ── the summary ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_summary_carries_poradcas_proposal_with_his_instruction_on_every_card(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)

    found = batch_review.summary(db_session, version.id)

    assert found["status"] == "done"
    rows = {r["key"]: r for r in found["rows"]}
    d1, d2 = rows["d1"], rows["d2"]
    assert (d1["state"], d1["option_id"], d1["free_text"], d1["poradca_instruction"]) == ("agree", "a", None, None)
    assert (d2["state"], d2["option_id"], d2["poradca_label"], d2["builder_label"]) == (
        "disagree",
        "b",
        "Voľba B2",
        "Voľba A2",
    ), "where they disagree, Poradca's option is what the summary approves"
    assert (d2["poradca_why"], d2["poradca_instruction"]) == ("A2 by rozbila export.", INSTRUCTION_2)
    assert found["counts"] == {"agree": 1, "disagree": 1, "missing": 0, "decided": 0}


def test_poradcas_own_decision_is_his_proposal_word_for_word(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version, content=ANSWER.replace("voľba: Voľba B2", "voľba: „Vlastné rozhodnutie: dať oboje“"))

    d2 = {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}["d2"]

    assert (d2["state"], d2["option_id"], d2["free_text"]) == ("disagree", None, "dať oboje")


def test_an_answer_from_before_says_who_decides_and_no_card_is_left_out_for_it(db_session):
    version = _seed_consultation(db_session)
    old = ANSWER.replace("pokyn: —", "rozhoduje: manažér", 1)
    _reviewed(db_session, version, content=old)

    d1 = {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}["d1"]

    assert (d1["state"], d1["option_id"], d1["poradca_why"]) == ("agree", "a", "Zodpovedá Špecifikácii §3.")


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
        "Otázka 1? → Voľba A1 — schválené v súhrne: návrh Poradcu, Staviteľ navrhol to isté (schválil Zoltán Rausch)"
    )
    assert answer.payload["consultation_decision"]["via"] == batch_review.VIA
    rows = {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}
    assert rows["d1"]["state"] == "decided"


async def test_a_card_poradca_proposes_differently_is_approved_as_he_proposes_with_his_instruction(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)
    owner = _owner(db_session, version)
    owner.first_name, owner.last_name = "Zoltán", "Rausch"

    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "d2", "option_id": "b", "note": INSTRUCTION_2, "via": batch_review.VIA},
        acting_user=owner,
    )

    answer = (
        db_session.query(PipelineMessage)
        .filter_by(version_id=version.id, kind="answer")
        .order_by(PipelineMessage.seq.desc())
        .first()
    )
    assert answer.content == (
        "Otázka 2? → Voľba B2 — schválené v súhrne: návrh Poradcu, Staviteľ navrhol „Voľba A2“ (schválil Zoltán Rausch)"
    )
    assert answer.payload["consultation_decision"]["note"] == INSTRUCTION_2, "the Staviteľ gets Poradca's instruction"


async def test_poradcas_own_decision_is_approved_word_for_word(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version, content=ANSWER.replace("voľba: Voľba B2", "voľba: Vlastné rozhodnutie: dať oboje"))

    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "d2", "free_text": "dať oboje", "note": INSTRUCTION_2, "via": batch_review.VIA},
    )

    assert {r["key"]: r for r in batch_review.summary(db_session, version.id)["rows"]}["d2"]["state"] == "decided"


async def test_nothing_but_poradcas_proposal_is_approved_in_the_summary(db_session):
    version = _seed_consultation(db_session)
    _reviewed(db_session, version)

    for payload in (
        {"decision_key": "d2", "option_id": "a", "note": INSTRUCTION_2},  # the Staviteľ's option, not Poradca's
        {"decision_key": "d1", "option_id": "b"},  # an option nobody proposed
        {"decision_key": "d2", "option_id": "b"},  # without Poradca's instruction
        {"decision_key": "d2", "option_id": "b", "note": "iný pokyn"},  # with another one
    ):
        with pytest.raises(OrchestratorError, match="v súhrne sa schvaľuje len návrh Poradcu"):
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


# ── assessing again („Posúdiť znova“) ────────────────────────────────────────────────────────────────────────────


def test_the_cards_go_to_poradca_again_and_the_old_summary_is_gone(db_session, poradca):
    version = _seed_consultation(db_session)
    _owner(db_session, version).batch_review = True
    old = _reviewed(db_session, version)
    old_id = old.id

    record = batch_review.again(db_session, version.id)

    assert record.id != old_id and len(poradca) == 1 and "pokyn:" in poradca[0][1]
    assert db_session.get(BatchReview, old_id) is None
    assert batch_review.summary(db_session, version.id)["status"] == "running"


def test_assessing_again_is_refused_while_poradca_answers_and_without_the_switch(db_session, poradca):
    version = _seed_consultation(db_session)
    with pytest.raises(batch_review.Refused, match="nemá rýchlu cestu s Poradcom zapnutú"):
        batch_review.again(db_session, version.id)

    _owner(db_session, version).batch_review = True
    _reviewed(db_session, version, content="", status=RUNNING)
    with pytest.raises(batch_review.Refused, match="Poradca karty práve posudzuje"):
        batch_review.again(db_session, version.id)
    assert poradca == []


def test_assessing_again_is_refused_off_the_cards(db_session, poradca):
    resting = _seed_consultation(db_session, status="awaiting_manazer")
    _owner(db_session, resting).batch_review = True
    with pytest.raises(batch_review.Refused, match="Stavba nestojí na kartách rozhodnutí"):
        batch_review.again(db_session, resting.id)
    assert poradca == []


def _pipeline_client(db, user) -> TestClient:
    from backend.api.routes.pipeline import router
    from backend.core.security import get_current_user
    from backend.db.session import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/pipeline")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_the_route_assesses_again_and_says_why_not(db_session, poradca):
    version = _seed_consultation(db_session)
    owner = _owner(db_session, version)
    client = _pipeline_client(db_session, owner)

    refused = client.post(f"/api/v1/pipeline/{version.id}/batch-review/again")
    assert refused.status_code == 409 and "nemá rýchlu cestu" in refused.json()["detail"]

    owner.batch_review = True
    done = client.post(f"/api/v1/pipeline/{version.id}/batch-review/again")
    assert done.status_code == 200 and done.json()["status"] == "running" and len(poradca) == 1


# ── Poradca's view of the cards ──────────────────────────────────────────────────────────────────────────────────


def test_poradca_hears_of_a_card_on_turn_only_without_the_summary(db_session):
    from backend.services.poradca.tools import _cards_lines

    version = _seed_consultation(db_session)
    assert "na rade je karta 1 z 2" in "\n".join(_cards_lines(db_session, version.id))

    _reviewed(db_session, version)
    cards = "\n".join(_cards_lines(db_session, version.id))
    assert "na rade" not in cards and "rozhodnúť sa dá ktorákoľvek nerozhodnutá karta" in cards


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
    assert '`<posudok karta="N">`' in poradca and "`pokyn:`" in poradca and "`rozhoduje:`" not in poradca
    assert "Pokyn píš ku **karte, ktorú Manažér práve rozhoduje**" in poradca
