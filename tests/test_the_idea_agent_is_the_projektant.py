"""DEV-343 — the agent of Nápady is the Projektant; the Poradca stays with projects.

Director 10.11.2026: „V záložke Nápady sme nazvali agenta ako Poradca, aj on tak vypisuje, ale my v kokpite máme už
agenta Poradca, a keď rozprávame s chlapcami medzi sebou pojem Poradca je chaotická“ — the name Projektant: „Súhlasím“.
One rule names the agent (``runner.agent_of``): a conversation without a project — a new idea — is the Projektant's;
once its project is founded, the talk goes on with the project's Poradca.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.api.routes import poradca as poradca_routes
from backend.db.models.idea import Idea
from backend.db.models.poradca import RUNNING, PoradcaConversation, PoradcaMessage
from backend.schemas.poradca import PoradcaStatus
from backend.services.poradca import ideas, runner
from tests.test_ideas_talk_without_a_project import _client, _user

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-ideas-charter.md"


def test_an_idea_without_a_project_is_the_projektants_and_a_projects_talk_the_poradcas():
    assert runner.agent_of(PoradcaConversation(project_id=None, idea_id=uuid.uuid4())) == runner.PROJEKTANT
    assert runner.agent_of(PoradcaConversation(project_id=uuid.uuid4(), idea_id=uuid.uuid4())) == runner.PORADCA
    assert runner.agent_of(PoradcaConversation(project_id=uuid.uuid4(), idea_id=None)) == runner.PORADCA
    assert (runner.PROJEKTANT.name, runner.PROJEKTANT.whom, runner.PROJEKTANT.to_whom) == (
        "Projektant",
        "Projektanta",
        "Projektantovi",
    )


def test_the_projektant_introduces_itself_so_and_knows_the_poradca_is_the_projects():
    text = CHARTER.read_text(encoding="utf-8")
    assert text.startswith("# Projektant v Nápadoch")
    assert "Si **Projektant** kokpitu NEX Studio" in text and "Si **Poradca**" not in text
    assert "rozhovor pokračuje s Poradcom projektu" in text


@pytest.mark.parametrize("sentence", [ideas.NO_BRIEF, ideas.REWRITTEN, ideas.NO_WORK_KIND])
def test_the_ideas_sentences_name_the_projektant(sentence):
    assert "Projektant" in sentence and "Poradc" not in sentence


def test_a_changed_charter_is_handed_to_the_projektant_under_its_name():
    prompt = runner._prompt("Nápad", "Zoltán", "Ďalej?", "nové pravidlá", runner.PROJEKTANT)
    assert prompt.startswith("[Pravidlá Projektanta sa od začiatku tohto rozhovoru zmenili.")
    project = runner._prompt("Projekt", "Zoltán", "Ďalej?", "nové pravidlá", runner.PORADCA)
    assert project.startswith("[Pravidlá Poradcu sa od začiatku tohto rozhovoru zmenili.")


def _idea_talk(db, me) -> PoradcaConversation:
    idea = Idea(author_id=me.id)
    db.add(idea)
    db.flush()
    talk = PoradcaConversation(author_id=me.id, title="Sklad", idea_id=idea.id, claude_session_id=str(uuid.uuid4()))
    db.add(talk)
    db.flush()
    return talk


def test_a_busy_idea_says_the_projektant_answers(db_session):
    me = _user(db_session)
    talk = _idea_talk(db_session, me)
    db_session.add(PoradcaMessage(conversation_id=talk.id, author="poradca", content="", status=RUNNING))
    db_session.flush()

    with pytest.raises(runner.PoradcaBusy) as busy:
        runner.ask(db_session, talk, "A ďalej?", me)

    assert str(busy.value) == "V tomto rozhovore už Projektant odpovedá — počkaj, alebo ho zastav."


def test_an_idea_that_cannot_run_says_the_projektant_cannot(db_session, monkeypatch):
    me = _user(db_session)
    talk = _idea_talk(db_session, me)
    monkeypatch.setattr(
        poradca_routes.readiness,
        "status",
        lambda db: PoradcaStatus(ready=False, problems=["chýba obraz"], running=0, max_concurrent=3),
    )

    response = _client(db_session, me).post(
        f"/api/v1/poradca/conversations/{talk.id}/messages", json={"question": "A ďalej?"}
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "Projektant teraz nevie bežať: chýba obraz"
