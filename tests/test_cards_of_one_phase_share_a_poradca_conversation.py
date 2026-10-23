"""DEV-138 — „Overiť s Poradcom“ takes every card of one version and phase into ONE conversation.

Director 16.10.2026, NEX Inbox 1.9.0 waiting in Návrh on 9 decision cards: „ak kliknem v karte agenta na tlačidlo
"Overiť s Poradcom" každý krát otvorí nový rozhovor. Takou rýchlosťou o chvíľku budem mať zahlcený celý zoznam
rozhovorov.“ Cards 1 and 2 had become two conversations with one question each — and Poradca sees every card of the
version in any conversation about it, so a new one never helped.

The conversation remembers on the server that it is the one for the cards of a version and phase; the button finds
it again (the page asks ``review-conversation``) and founds a new one only when there is none. A hand-written
conversation, a deleted one, somebody else's and one of another version or phase are never taken over.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.api.routes import poradca as poradca_routes
from backend.db.models.foundation import User
from backend.db.models.pipeline import STAGE_VALUES, PipelineState
from backend.db.models.poradca import PoradcaConversation
from backend.db.models.versions import Version
from backend.services.poradca import review
from tests.test_poradca_api import _client, _fake_run, _project, _user  # noqa: F401 — _fake_run is autouse there

QUESTION = "Rozhodnutie 1 z 9 — Faktúra má IČO, ktoré je platné, ale takú firmu v zozname dodávateľov nemáme."


def _version(db: Any, project: Any, number: str = "1.9.0", stage: str | None = "navrh") -> Version:
    v = Version(project_id=project.id, version_number=number)
    db.add(v)
    db.flush()
    if stage is not None:
        db.add(
            PipelineState(
                version_id=v.id,
                flow_type="new_version",
                current_stage=stage,
                current_actor="ai_agent",
                status="blocked",
                block_reason="decision_needed",
            )
        )
        db.flush()
    return v


def _found(client: Any, project: Any, version: Version, *, review_card: bool = True, question: str = QUESTION) -> dict:
    r = client.post(
        f"/api/v1/poradca/projects/{project.slug}/conversations",
        json={"question": question, "version_id": str(version.id), "review": review_card},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _found_by_button(client: Any, project: Any, version: Version) -> str | None:
    r = client.get(
        f"/api/v1/poradca/projects/{project.slug}/review-conversation", params={"version_id": str(version.id)}
    )
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def test_the_second_card_of_a_phase_finds_the_conversation_the_first_one_founded(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project)
    c = _client(db_session, owner)

    assert _found_by_button(c, project, version) is None
    first = _found(c, project, version)
    assert first["title"] == "1.9.0 — rozhodnutia v Návrhu"
    assert _found_by_button(c, project, version) == first["id"]

    # The second card goes into it as the next question — the list keeps one conversation.
    second = c.post(f"/api/v1/poradca/conversations/{first['id']}/messages", json={"question": "Rozhodnutie 2 z 9"})
    assert second.status_code == 200, second.text
    listed = c.get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()
    assert [x["id"] for x in listed] == [first["id"]]
    assert _found_by_button(c, project, version) == first["id"]


def test_another_phase_of_the_same_version_has_a_conversation_of_its_own(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project)
    c = _client(db_session, owner)
    navrh = _found(c, project, version)

    state = db_session.query(PipelineState).filter(PipelineState.version_id == version.id).one()
    state.current_stage = "programovanie"
    db_session.flush()

    assert _found_by_button(c, project, version) is None
    programovanie = _found(c, project, version)
    assert programovanie["title"] == "1.9.0 — rozhodnutia v Programovaní"
    assert _found_by_button(c, project, version) == programovanie["id"] != navrh["id"]


def test_a_hand_written_conversation_about_the_version_is_never_taken_over(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project)
    c = _client(db_session, owner)

    own = _found(c, project, version, review_card=False, question="Prečo agent stojí?")
    assert own["title"] == "Prečo agent stojí?"
    assert _found_by_button(c, project, version) is None


def test_a_deleted_conversation_is_not_found_again(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project)
    c = _client(db_session, owner)
    first = _found(c, project, version)

    conversation = db_session.get(PoradcaConversation, uuid.UUID(first["id"]))
    conversation.deleted_at = datetime.now(timezone.utc)
    db_session.flush()
    assert _found_by_button(c, project, version) is None


def test_somebody_elses_conversation_is_not_taken_over(db_session):
    owner = _user(db_session)
    admin = db_session.query(User).filter(User.username == "admin").first() or _user(db_session, "admin")
    project = _project(db_session, owner)
    version = _version(db_session, project)

    admins = _found(_client(db_session, admin), project, version)
    assert _found_by_button(_client(db_session, owner), project, version) is None
    assert _found_by_button(_client(db_session, admin), project, version) == admins["id"]


def test_the_conversation_of_another_version_is_not_taken_over(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    older = _version(db_session, project, "1.8.0")
    newer = _version(db_session, project, "1.9.0")
    c = _client(db_session, owner)

    _found(c, project, older)
    assert _found_by_button(c, project, newer) is None


def test_a_version_that_was_never_built_has_no_phase_and_keeps_the_question_as_title(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project, stage=None)
    c = _client(db_session, owner)

    body = _found(c, project, version)
    assert body["title"] == poradca_routes._title(QUESTION)
    assert db_session.get(PoradcaConversation, uuid.UUID(body["id"])).review_stage is None
    assert _found_by_button(c, project, version) is None


def test_a_version_of_another_project_is_refused(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    foreign = _version(db_session, _project(db_session, owner))
    r = _client(db_session, owner).get(
        f"/api/v1/poradca/projects/{project.slug}/review-conversation", params={"version_id": str(foreign.id)}
    )
    assert r.status_code == 422


def test_every_phase_has_its_place_in_the_title():
    assert set(review.STAGE_PLACES) == set(STAGE_VALUES)


def test_the_questions_about_a_finished_version_share_its_conversation_after_the_build(db_session):
    """DEV-191 — „Opýtaj sa Poradcu“ at a finished version asks like a card: the first question founds the version's
    conversation after the build, the next goes on in it. It holds questions, not decisions — its title says so."""
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = _version(db_session, project, number="1.10.1", stage="done")
    c = _client(db_session, owner)

    first = _found(c, project, version, question="Verzia 1.10.1 projektu NEX Inbox je hotová. Zhrň mi, čo priniesla.")

    assert first["title"] == "1.10.1 — po dokončení"
    assert _found_by_button(c, project, version) == first["id"]
