"""DEV-218 — the agent that meets a ticket without its content asks for it; Poradca reads the ticket by its number and
one click attaches its content, fresh from Plane, to the answer for the agent as a file.

24.10.2026 the brief of NEX Knowledge 0.1.0 ended with „Podklad: návrh NEX Knowledge schválený Directorom 22.10.2026
… a plán verzií schválený 23.10.2026“ — without the content. The design is ticket MAGER-66 in Plane, the plan a
comment of DEV-186; the agent's turn does not see Plane, so its first question asked for both documents and the
Director had to send them in an answer. Director: „ak Agent sa narazí na tiket (napríklad MAGER-66) bez priloženého
obsahu tak požiadá (tlačítko ako to už máme v iných mistach) Poradcu aby doplnil chýbajúce informácie“, „Áno,
súhlasím, implementuj DEV-218“.

The cockpit's account reads only the registers it is a member of — measured 24.10.2026: DEV only; MAGER and ICCINT
answer 403, PRIVATE and TIBI it does not see. What it cannot read is said, so nobody waits for a file that will not
come.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes import poradca as poradca_routes
from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import claude_agent, plane_tickets
from backend.services.pipeline_ws import registry
from backend.services.poradca import tools
from backend.services.poradca.mcp_server import ToolError
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_poradca_api import _project, _user
from tests.test_poradca_tools_db import world  # noqa: F401 — the fixture is used by name
from tests.test_the_manager_attaches_a_file_for_the_agent import _git, _repo

ROOT = Path(__file__).resolve().parents[1]

DESIGN = (
    "<p>Robí: Andros Dedo</p><p>Návrh NEX Knowledge — body 1–20.</p>"
    "<p>1. Dokumenty sa ukladajú po skupinách.<br>2. Hľadá sa vo všetkých skupinách naraz.</p>"
)


@pytest.fixture
def plane(monkeypatch):
    monkeypatch.setattr(settings, "display_timezone", "Europe/Bratislava")
    fake = FakePlane()
    fake.registers += [
        {"id": "p2", "identifier": "MAGER", "is_member": True},
        {"id": "p3", "identifier": "ICCINT", "is_member": False},
    ]
    fake.members = [
        {"id": "u-zoltan", "first_name": "Zoltán", "last_name": "Rausch", "display_name": "zoltan"},
        {"id": "u-dedo", "first_name": "", "last_name": "", "display_name": "Dedo"},
    ]
    fake.issues["i66"] = {
        "id": "i66",
        "project": "p2",
        "sequence_id": 66,
        "name": "Návrh NEX Knowledge",
        "description_html": DESIGN,
        "state": "s-done",
        "labels": [],
        "assignees": [],
        "updated_at": "2026-10-23T18:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    fake.comments["i66"] = [
        {
            "comment_html": "<p>Máger Dedo — doplnil som bod 20.</p>",
            "actor": "u-dedo",
            "created_at": "2026-10-23T09:00:00Z",
            "deleted_at": None,
        },
        {
            "comment_html": "<p>Schvaľujem body 1–20.</p>",
            "actor": "u-zoltan",
            "created_at": "2026-10-22T08:15:00Z",
            "deleted_at": None,
        },
        {
            "comment_html": "<p>omyl</p>",
            "actor": "u-dedo",
            "created_at": "2026-10-22T08:20:00Z",
            "deleted_at": "2026-10-22T08:21:00Z",
        },
    ]
    fake.issues["i186"] = {
        "id": "i186",
        "sequence_id": 186,
        "name": "Plán verzií NEX Knowledge",
        "description_html": "<p>Robí: Andros Dedo</p><p>0.1.0 základ, 0.2.0 hľadanie.</p>",
        "state": "s-ip",
        "labels": [],
        "assignees": [],
        "updated_at": "2026-10-23T10:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


MAGER_66 = (
    "# MAGER-66 — Návrh NEX Knowledge\n"
    "\n"
    "Tiket z evidencie MAGER v Plane · stav: Done · naposledy zmenený 23.10.2026 20:00\n"
    "http://plane.test/icc/projects/p2/issues/i66\n"
    "\n"
    "Návrh NEX Knowledge — body 1–20.\n"
    "\n"
    "1. Dokumenty sa ukladajú po skupinách.\n"
    "2. Hľadá sa vo všetkých skupinách naraz.\n"
    "\n"
    "## Komentáre (2, od najstaršieho)\n"
    "\n"
    "### Zoltán Rausch · 22.10.2026 10:15\n"
    "\n"
    "Schvaľujem body 1–20.\n"
    "\n"
    "### Dedo · 23.10.2026 11:00\n"
    "\n"
    "Máger Dedo — doplnil som bod 20.\n"
)


def _tool(world, name: str):  # noqa: F811
    t = world["tools"]
    return next(
        tool
        for tool in tools.build_tools(project_id=t.project_id, version_id=t.version_id, user_id=t.user_id)
        if tool.name == name
    )


# ── Poradca reads a ticket by its number ─────────────────────────────────────────────────────────────────────────


async def test_poradca_reads_a_ticket_of_another_register_by_its_number_with_its_comments(world, plane):  # noqa: F811
    assert await _tool(world, "plane_tiket").handler({"cislo": "MAGER-66"}) == MAGER_66


async def test_a_number_in_small_letters_is_read_too(world, plane):  # noqa: F811
    assert (await _tool(world, "plane_tiket").handler({"cislo": " mager-66 "})).startswith("# MAGER-66 — ")


async def test_a_ticket_without_comments_says_so(world, plane):  # noqa: F811
    text = await _tool(world, "plane_tiket").handler({"cislo": "DEV-186"})

    assert text.endswith("0.1.0 základ, 0.2.0 hľadanie.\n\nKomentáre: žiadne.\n"), text
    assert "Robí:" not in text, "who works on the ticket is not what it says"


@pytest.mark.parametrize(
    ("number", "sentence"),
    [
        (
            "ICCINT-271",
            "Účet kokpitu nie je členom evidencie ICCINT v Plane, preto tiket ICCINT-271 neprečíta. O členstve kokpitu "
            "v evidencii rozhoduje Director.",
        ),
        ("PRIVATE-3", "Evidenciu PRIVATE účet kokpitu v Plane nevidí — tiket PRIVATE-3 neprečíta."),
        ("DEV-999", "Tiket DEV-999 v Plane nie je."),
        ("MAGER66", "„MAGER66“ nie je číslo tiketu — napíš ho tak, ako ho píše Plane, napríklad MAGER-66."),
    ],
)
async def test_what_the_cockpit_cannot_read_is_said(world, plane, number, sentence):  # noqa: F811
    with pytest.raises(ToolError) as refused:
        await _tool(world, "plane_tiket").handler({"cislo": number})

    assert str(refused.value) == sentence


async def test_an_archived_ticket_without_a_text_says_both(world, plane):  # noqa: F811
    plane.issues["i186"].update(archived_at="2026-10-24T08:00:00Z", description_html="<p>Robí: Andros Dedo</p>")

    text = await _tool(world, "plane_tiket").handler({"cislo": "DEV-186"})

    assert text.splitlines()[2] == (
        "Tiket z evidencie DEV v Plane · stav: In Progress · archivovaný · naposledy zmenený 23.10.2026 12:00"
    )
    assert "\n(bez popisu)\n" in text


async def test_a_deleted_ticket_is_said_not_read(world, plane):  # noqa: F811
    plane.issues["i66"]["deleted_at"] = "2026-10-24T08:00:00Z"

    with pytest.raises(ToolError) as refused:
        await _tool(world, "plane_tiket").handler({"cislo": "MAGER-66"})

    assert str(refused.value) == "Tiket MAGER-66 je v Plane zmazaný."


# ── one click attaches the ticket to the answer for the agent ────────────────────────────────────────────────────

ANSWER = (
    "Agent pýta návrh a plán verzií. Návrh je tiket MAGER-66 (body 1–20), plán verzií tiket DEV-186.\n"
    "<tiket-pre-agenta>MAGER-66, dev-186</tiket-pre-agenta>"
)


@pytest.fixture
def setup(db_session, plane, monkeypatch):
    sent: list[dict] = []

    async def _broadcast(version_id, frame):
        sent.append(frame)

    monkeypatch.setattr(registry, "broadcast", _broadcast)
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = Version(project_id=project.id, version_number="0.1.0")
    db_session.add(version)
    db_session.flush()
    root = _repo(claude_agent.PROJECTS_ROOT / project.slug)
    conversation = PoradcaConversation(
        project_id=project.id,
        version_id=version.id,
        author_id=owner.id,
        title="Podklady",
        claude_session_id=uuid.uuid4(),
    )
    db_session.add(conversation)
    db_session.flush()

    def answer(content: str = ANSWER, *, status: str = "done") -> PoradcaMessage:
        msg = PoradcaMessage(conversation_id=conversation.id, author="poradca", content=content, status=status)
        db_session.add(msg)
        db_session.commit()
        return msg

    def client(user=owner) -> TestClient:
        app = FastAPI()
        app.include_router(poradca_routes.router, prefix="/api/v1/poradca")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return {
        "db": db_session,
        "owner": owner,
        "version": version,
        "conversation": conversation,
        "root": root,
        "answer": answer,
        "client": client,
        "sent": sent,
        "plane": plane,
    }


def _attach(setup, msg):
    return setup["client"]().post(f"/api/v1/poradca/messages/{msg.id}/agent-tickets")


def test_the_answer_names_the_tickets_its_button_attaches(setup):
    msg = setup["answer"]()

    shown = setup["client"]().get(f"/api/v1/poradca/conversations/{setup['conversation'].id}").json()

    [read] = [m for m in shown["messages"] if m["id"] == str(msg.id)]
    assert read["agent_tickets"] == ["MAGER-66", "DEV-186"]


def test_an_answer_without_the_block_names_no_ticket(setup):
    msg = setup["answer"]("Návrh je tiket MAGER-66 — ale bez značky.")

    shown = setup["client"]().get(f"/api/v1/poradca/conversations/{setup['conversation'].id}").json()

    [read] = [m for m in shown["messages"] if m["id"] == str(msg.id)]
    assert read["agent_tickets"] == []


def test_one_click_attaches_each_ticket_fresh_from_plane_and_hands_back_the_lines_for_the_answer(setup):
    msg = setup["answer"]()
    # the ticket changed after Poradca read it — the file carries what Plane holds at the click
    setup["plane"].issues["i186"]["description_html"] = "<p>0.1.0 základ, 0.2.0 hľadanie, 0.3.0 rozhranie.</p>"

    r = _attach(setup, msg)

    assert r.status_code == 200, r.text
    root = setup["root"]
    assert (root / "private" / "MAGER-66.md").read_text(encoding="utf-8") == MAGER_66
    assert "0.3.0 rozhranie." in (root / "private" / "DEV-186.md").read_text(encoding="utf-8")
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
    size_66 = len(MAGER_66.encode())
    size_186 = (root / "private" / "DEV-186.md").stat().st_size
    assert size_66 < 1024 and size_186 < 1024, "the sizes below are said in bytes"
    assert r.json() == {
        "attached": ["private/MAGER-66.md", "private/DEV-186.md"],
        "answer_text": (
            f"Obsah tiketu MAGER-66 z Plane (Návrh NEX Knowledge): `private/MAGER-66.md` ({size_66} B) — "
            "leží v koreni projektu v priečinku private/, mimo gitu.\n"
            f"Obsah tiketu DEV-186 z Plane (Plán verzií NEX Knowledge): `private/DEV-186.md` ({size_186} B) — "
            "leží v koreni projektu v priečinku private/, mimo gitu."
        ),
    }


def test_the_build_history_says_who_attached_which_ticket(setup):
    msg = setup["answer"]()

    _attach(setup, msg)

    history = list(
        setup["db"]
        .execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == setup["version"].id)
            .order_by(PipelineMessage.seq)
        )
        .scalars()
    )
    who = setup["owner"].username
    assert [m.content.split(" (")[0] for m in history] == [
        f"{who} priložil tiket MAGER-66 z Plane ako súbor private/MAGER-66.md",
        f"{who} priložil tiket DEV-186 z Plane ako súbor private/DEV-186.md",
    ]
    assert history[0].payload["private_file"] == {
        "action": "uploaded",
        "path": "private/MAGER-66.md",
        "size_bytes": len(MAGER_66.encode()),
        "by": who,
        "plane_ticket": "MAGER-66",
    }
    assert [frame["type"] for frame in setup["sent"]] == ["message_added", "message_added"]


def test_attaching_again_replaces_the_file_with_what_the_ticket_says_now(setup):
    msg = setup["answer"]()
    _attach(setup, msg)
    setup["plane"].issues["i66"]["description_html"] = "<p>Návrh NEX Knowledge — body 1–21.</p>"

    assert _attach(setup, msg).status_code == 200

    files = sorted(p.name for p in (setup["root"] / "private").iterdir())
    assert files == ["DEV-186.md", "MAGER-66.md"]
    assert "body 1–21." in (setup["root"] / "private" / "MAGER-66.md").read_text(encoding="utf-8")


def test_a_ticket_the_cockpit_cannot_read_attaches_nothing_and_says_why(setup):
    msg = setup["answer"]("<tiket-pre-agenta>MAGER-66, ICCINT-271</tiket-pre-agenta>")

    r = _attach(setup, msg)

    assert r.status_code == 502
    assert r.json()["detail"] == (
        "Účet kokpitu nie je členom evidencie ICCINT v Plane, preto tiket ICCINT-271 neprečíta. O členstve kokpitu "
        "v evidencii rozhoduje Director."
    )
    assert not (setup["root"] / "private").exists(), "half of the tickets is not an answer"


def test_a_secret_in_a_ticket_does_not_reach_the_file(setup):
    setup["plane"].issues["i66"]["description_html"] = "<p>Pripojenie: postgresql://app:Tajne-heslo-42@db/app</p>"
    msg = setup["answer"]("<tiket-pre-agenta>MAGER-66</tiket-pre-agenta>")

    assert _attach(setup, msg).status_code == 200

    text = (setup["root"] / "private" / "MAGER-66.md").read_text(encoding="utf-8")
    assert "Tajne-heslo-42" not in text and "postgresql://app:‹skryté›@db/app" in text


@pytest.mark.parametrize(
    ("content", "scope", "sentence"),
    [
        ("Bez značky MAGER-66.", True, "V tejto odpovedi nie je tiket pre agenta."),
        (
            "<tiket-pre-agenta>MAGER-66</tiket-pre-agenta>",
            False,
            "Rozhovor je o celom projekte — hore vyber verziu, do ktorej stavby tiket patrí.",
        ),
    ],
)
def test_an_answer_that_names_no_ticket_or_no_build_attaches_nothing(setup, content, scope, sentence):
    if not scope:
        setup["conversation"].version_id = None
    msg = setup["answer"](content)

    r = _attach(setup, msg)

    assert (r.status_code, r.json()["detail"]) == (422, sentence)
    assert not (setup["root"] / "private").exists()


def test_an_unfinished_answer_attaches_nothing(setup):
    msg = setup["answer"](status="running")

    assert _attach(setup, msg).status_code == 422


def test_someone_elses_answer_does_not_exist_for_you(setup):
    msg = setup["answer"]()
    stranger = _user(setup["db"])

    r = setup["client"](stranger).post(f"/api/v1/poradca/messages/{msg.id}/agent-tickets")

    assert r.status_code == 404
    assert not (setup["root"] / "private").exists()


# ── the rules say it ─────────────────────────────────────────────────────────────────────────────────────────────


def _charter(name: str) -> str:
    return " ".join((ROOT / "templates" / name).read_text(encoding="utf-8").split())


def test_the_agent_asks_for_a_ticket_it_has_no_content_of_by_its_number():
    charter = _charter("ai-agent-charter.md")

    assert "narazíš na tiket (napríklad MAGER-66) a jeho obsah nemáš" in charter
    assert "tiket v nej pomenuj jeho číslom" in charter


def test_poradca_reads_the_ticket_and_offers_the_block_instead_of_retyping_it():
    charter = _charter("poradca-charter.md")

    assert "<tiket-pre-agenta>MAGER-66, DEV-186</tiket-pre-agenta>" in charter
    assert "obsah tiketu do odpovede neprepisuj" in charter
    assert "„Priložiť k odpovedi agentovi“" in charter
