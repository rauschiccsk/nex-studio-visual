"""DEV-119 — Poradca proposes a ticket in Plane; a person's click founds it after a preview.

Director 14.10.2026: „Chcel by som aby poradca vedel zakladať do plane tiket na dve veci, buď nejaký problém s NSV,
ten tiket potom budeš riešiť ty, alebo pre konkrétny projekt, ktorý robí a čo pojde do nasledujúcej verzie, alebo do
rýchlej opravy.“ A problem of the cockpit goes to the module NEX Studio Visual for Andros Dedo with the context the
cockpit knows; work on the project goes to its module with the work kind and the proposed way — a proposal only,
no version is created or started (DEV-29). The preview names similar open tickets; a second click founds nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.config.settings import settings
from backend.db.models.backlog import BacklogItem
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.versions import Version
from backend.services import plane_tickets
from backend.services.poradca import tickets
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_orchestrator_v2_vizual import _seed_state
from tests.test_poradca_api import _client, _project, _user

NSV_ANSWER = (
    "Toto je chyba kokpitu, nie projektu.\n"
    "<tiket-nsv>\nTlačidlo Nasadiť pri UAT nič nerobí\n\nPo kliknutí sa neobjaví ani hláška, ani priebeh.\n</tiket-nsv>"
)
PROJECT_ANSWER = (
    "Patrí to do projektu.\n"
    '<tiket-projektu druh="oprava" cesta="rychla-oprava">\nImport ISDOC nečíta číslo faktúry\n\n'
    "Číslo faktúry je v ISDOC len vo variabilnom symbole.\n</tiket-projektu>"
)


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    fake.modules.append({"id": "m-nsv", "name": "NEX Studio Visual"})
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


def _seed(plane: FakePlane, issue_id: str, name: str, *, module: str, state: str = "s-todo") -> None:
    plane.issues[issue_id] = {
        "id": issue_id,
        "sequence_id": int(issue_id.removeprefix("x")),
        "name": name,
        "description_html": "<p>Robí: Andros Dedo</p>",
        "state": state,
        "labels": [],
        "assignees": [],
        "updated_at": "2026-10-15T09:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    plane.module_issues.setdefault(module, []).append(issue_id)


@pytest.fixture
def setup(db_session, plane):
    owner = _user(db_session)
    owner.first_name, owner.last_name = "Zoltán", "Rausch"
    project = _project(db_session, owner)
    version = Version(project_id=project.id, version_number="1.3.0", name="dev")
    db_session.add(version)
    db_session.flush()
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    state.status, state.next_action = "awaiting_manazer", "Pozri živý náhľad a schváľ vizuál."
    conversation = PoradcaConversation(
        project_id=project.id,
        version_id=version.id,
        author_id=owner.id,
        title="Prečo nejde nasadiť",
        claude_session_id=uuid.uuid4(),
    )
    db_session.add(conversation)
    db_session.flush()
    return owner, project, version, conversation, _client(db_session, owner)


def _answer(db, conversation, content: str, status: str = "done") -> PoradcaMessage:
    msg = PoradcaMessage(conversation_id=conversation.id, author="poradca", content=content, status=status)
    db.add(msg)
    db.flush()
    return msg


def _created(plane: FakePlane) -> list[dict]:
    return [i for i in plane.issues.values() if not i["id"].startswith("x")]


def _label_names(plane: FakePlane, issue: dict) -> set[str]:
    names = {lab["id"]: lab["name"] for lab in plane.labels}
    return {names[i] for i in issue["labels"]}


def _modules_of(plane: FakePlane, issue_id: str) -> list[str]:
    names = {m["id"]: m["name"] for m in plane.modules}
    return [names[m] for m, ids in plane.module_issues.items() if issue_id in ids]


# ── what the answer says ───────────────────────────────────────────────────────────────────────────────────────


def test_an_answer_with_a_ticket_block_says_what_it_proposes(setup, db_session):
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, NSV_ANSWER)
    _answer(db_session, conversation, "Len odpoveď bez návrhu.")
    nsv, plain = client.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["messages"][-2:]
    assert nsv["ticket_proposal"] == {"kind": "nsv", "title": "Tlačidlo Nasadiť pri UAT nič nerobí"}
    assert plain["ticket_proposal"] is None
    assert nsv["plane_identifier"] is None


def test_an_unfinished_answer_proposes_nothing(setup, db_session):
    _, _, _, conversation, client = setup
    msg = _answer(db_session, conversation, NSV_ANSWER, status="running")
    last = client.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["messages"][-1]
    assert last["ticket_proposal"] is None
    assert client.get(f"/api/v1/poradca/messages/{msg.id}/plane-ticket").status_code == 422


# ── a problem of the cockpit ───────────────────────────────────────────────────────────────────────────────────


def test_the_preview_of_a_cockpit_ticket_names_dedo_the_context_and_similar_open_tickets(
    setup, db_session, plane, monkeypatch
):
    _, project, _, conversation, client = setup
    monkeypatch.setattr(settings, "app_public_url", "https://studio.test/")
    _seed(plane, "x40", "Tlačidlo Nasadiť pri UAT zašedne bez dôvodu", module="m-nsv")
    _seed(plane, "x41", "Tlačidlo Nasadiť pri UAT nič nerobí po obnovení", module="m-nsv", state="s-done")
    _seed(plane, "x42", "Plán úloh je prázdny", module="m-nsv")
    _seed(plane, "x43", "Tlačidlo Uložiť v nastaveniach", module="m-nsv")  # one common word is not similar
    msg = _answer(db_session, conversation, NSV_ANSWER)

    r = client.get(f"/api/v1/poradca/messages/{msg.id}/plane-ticket")

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["title"], body["ticket_type"]) == ("nsv", "Tlačidlo Nasadiť pri UAT nič nerobí", "Bug")
    assert body["description"] == "Po kliknutí sa neobjaví ani hláška, ani priebeh."
    assert (body["responsible_options"], body["responsible"]) == (["Andros Dedo"], "Andros Dedo")
    assert body["context"] == [
        f"Projekt: {project.name}",
        "Verzia: v1.3.0",
        "Stavba: fáza vizual, stav awaiting_manazer",
        "Čo ďalej podľa kokpitu: Pozri živý náhľad a schváľ vizuál.",
        f"Rozhovor Poradcu: https://studio.test/poradca?c={conversation.id}",
    ]
    assert [t["identifier"] for t in body["similar"]] == ["DEV-40"]
    assert _created(plane) == []  # the preview founds nothing


def test_a_click_founds_the_cockpit_ticket_for_dedo_once_with_the_context(setup, db_session, plane):
    _, project, _, conversation, client = setup
    msg = _answer(db_session, conversation, NSV_ANSWER)
    payload = {"title": "Nasadiť na UAT nič nerobí", "description": "Klik bez odozvy.", "ticket_type": "Bug"}

    first = client.post(f"/api/v1/poradca/messages/{msg.id}/plane-ticket", json=payload)
    second = client.post(f"/api/v1/poradca/messages/{msg.id}/plane-ticket", json=payload)

    assert first.status_code == 200, first.text
    assert first.json()["created"] is True and second.json()["created"] is False
    assert first.json()["plane_identifier"] == second.json()["plane_identifier"]
    [issue] = _created(plane)
    assert issue["name"] == "Nasadiť na UAT nič nerobí"
    assert _modules_of(plane, issue["id"]) == ["NEX Studio Visual"]
    assert _label_names(plane, issue) == {"Typ: Bug", "Zodpovedný: Andros Dedo"}
    text = issue["description_html"]
    assert text.startswith("<p>Robí: Andros Dedo</p><p>Klik bez odozvy.</p><p>Kontext z kokpitu:<br>")
    assert f"- Projekt: {project.name}<br>- Verzia: v1.3.0<br>" in text
    assert "(z rozhovoru s Poradcom)" in text
    shown = client.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["messages"][-1]
    assert (shown["plane_identifier"], shown["plane_url"]) == (
        first.json()["plane_identifier"],
        first.json()["plane_url"],
    )


def test_the_preview_of_a_founded_ticket_returns_it_without_asking_plane(setup, db_session, plane):
    _, _, _, conversation, client = setup
    msg = _answer(db_session, conversation, NSV_ANSWER)
    client.post(
        f"/api/v1/poradca/messages/{msg.id}/plane-ticket",
        json={"title": "Nasadiť nič nerobí", "description": "", "ticket_type": "Bug"},
    )
    plane.requests.clear()
    body = client.get(f"/api/v1/poradca/messages/{msg.id}/plane-ticket").json()
    assert body["plane_identifier"] and body["plane_url"]
    assert plane.requests == []


# ── work on the project ────────────────────────────────────────────────────────────────────────────────────────


def test_the_preview_of_a_project_ticket_carries_poradcas_work_kind_and_way(setup, db_session, plane):
    _, project, _, conversation, client = setup
    project.plane_module_id = "m-p"
    plane.modules.append({"id": "m-p", "name": project.name})
    _seed(plane, "x50", "Import ISDOC nečíta dobropis", module="m-p")
    _seed(plane, "x51", "Import ISDOC nečíta dobropis", module="m-nsv")
    msg = _answer(db_session, conversation, PROJECT_ANSWER)

    body = client.get(f"/api/v1/poradca/messages/{msg.id}/plane-ticket").json()

    assert (body["kind"], body["work_kind"], body["way"], body["ticket_type"]) == ("project", "fix", "fast_fix", "Bug")
    assert body["responsible_options"] == ["Andros Dedo", "Zoltán"]
    assert body["responsible"] == "Zoltán"
    assert body["context"] == []
    assert [t["identifier"] for t in body["similar"]] == ["DEV-50"]


def test_a_click_founds_the_project_ticket_as_a_proposal_and_starts_nothing(setup, db_session, plane):
    _, project, _, conversation, client = setup
    msg = _answer(db_session, conversation, PROJECT_ANSWER)
    versions = db_session.execute(select(func.count()).select_from(Version)).scalar_one()

    r = client.post(
        f"/api/v1/poradca/messages/{msg.id}/plane-ticket",
        json={
            "title": "Import ISDOC nečíta číslo faktúry",
            "description": "Číslo je len vo VS.",
            "ticket_type": "Bug",
            "work_kind": "fix",
            "way": "fast_fix",
            "responsible": "Zoltán",
        },
    )

    assert r.status_code == 200, r.text
    [issue] = _created(plane)
    assert _modules_of(plane, issue["id"]) == [project.name]
    assert _label_names(plane, issue) == {"Typ: Bug", "Zodpovedný: Zoltán", "Druh práce: Oprava (BUG)"}
    assert issue["description_html"].startswith(
        "<p>Robí: Zoltán</p><p>Číslo je len vo VS.</p><p>Návrh: do rýchlej opravy. O verzii rozhoduje Director — "
        "tiket ju nezakladá ani nespúšťa.</p>"
    )
    assert db_session.execute(select(func.count()).select_from(Version)).scalar_one() == versions
    assert db_session.execute(select(BacklogItem).where(BacklogItem.project_id == project.id)).first() is None


@pytest.mark.parametrize(
    "missing, sentence",
    [
        ({"way": None}, tickets.WAY_MISSING),
        ({"work_kind": None}, plane_tickets.WORK_KIND_MISSING),
        ({"responsible": "Niekto iný"}, "„Niekto iný“ v skupine „Zodpovedný“ evidencie DEV nie je."),
    ],
)
def test_a_project_ticket_without_what_it_needs_is_refused_before_plane_is_written(
    setup, db_session, plane, missing, sentence
):
    _, _, _, conversation, client = setup
    msg = _answer(db_session, conversation, PROJECT_ANSWER)
    payload = {
        "title": "Import ISDOC",
        "ticket_type": "Bug",
        "work_kind": "fix",
        "way": "version",
        "responsible": "Zoltán",
        **missing,
    }
    r = client.post(f"/api/v1/poradca/messages/{msg.id}/plane-ticket", json=payload)
    assert (r.status_code, r.json()["detail"]) == (400, sentence)
    assert _created(plane) == []


# ── refusals ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_an_answer_without_a_proposal_founds_nothing(setup, db_session, plane):
    _, _, _, conversation, client = setup
    msg = _answer(db_session, conversation, "Len odpoveď.")
    r = client.post(f"/api/v1/poradca/messages/{msg.id}/plane-ticket", json={"title": "x", "ticket_type": "Bug"})
    assert (r.status_code, r.json()["detail"]) == (422, "V tejto odpovedi nie je návrh tiketu.")
    assert _created(plane) == []


def test_someone_elses_answer_is_not_found(setup, db_session, plane):
    _, _, _, conversation, _ = setup
    msg = _answer(db_session, conversation, NSV_ANSWER)
    stranger = _client(db_session, _user(db_session))
    assert stranger.get(f"/api/v1/poradca/messages/{msg.id}/plane-ticket").status_code == 404
    r = stranger.post(f"/api/v1/poradca/messages/{msg.id}/plane-ticket", json={"title": "x", "ticket_type": "Bug"})
    assert r.status_code == 404 and _created(plane) == []


def test_when_plane_does_not_answer_the_answer_stays_without_a_ticket(setup, db_session, plane):
    _, _, _, conversation, client = setup
    msg = _answer(db_session, conversation, NSV_ANSWER)
    msg_id = msg.id
    plane.status = 503
    r = client.post(f"/api/v1/poradca/messages/{msg_id}/plane-ticket", json={"title": "x", "ticket_type": "Bug"})
    assert r.status_code == 502 and r.json()["detail"].startswith("Plane požiadavku odmietol: 503")
    kept = db_session.get(PoradcaMessage, msg_id)
    assert kept is None or kept.plane_issue_id is None


# ── the charter ────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_charter_asks_for_the_blocks_the_cockpit_reads_and_says_when_which():
    from backend.services.poradca import handoff

    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert f"<{handoff.BLOCK_TICKET_NSV}>" in charter
    assert f'<{handoff.BLOCK_TICKET_PROJECT} druh="oprava" cesta="rychla-oprava">' in charter
    assert "„Založiť tiket“" in charter
    for word in (*handoff.TICKET_WORK_KINDS, *handoff.TICKET_WAYS):
        assert f"`{word}`" in charter, f"the charter does not tell Poradca the word {word}"
    assert "Problém s kokpitom" in charter and "Práca na projekte" in charter and "Len do Zásobníka" in charter
