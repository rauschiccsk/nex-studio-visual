"""DEV-374 — Poradca sees the cockpit's own tickets, open and closed, before it proposes or reminds one.

13.11.2026, NEX Legal Watch 0.1.0: at 14:19 Poradca reminded the Manažér of a cockpit ticket on a name in the project
template („Ak si ho ešte nezaložil, nájdeš ho tam pod tlačidlom Založiť tiket“) — DEV-372 had been done and closed at
13:41. Poradca saw only its project's module and one DEV ticket by number; its charter even said of the similar tickets
the „Založiť tiket“ preview shows „ty ich nevidíš“, and the preview showed only open ones. Director, on the three fixes
of Poradca's inaccuracies: „Súhlasím“; on doing this one: „áno“.

One search for both: the preview keeps showing the open ones, Poradca's tool ``kokpit_tikety`` asks the same search for
the closed ones too.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.services import plane_tickets
from backend.services.poradca import tickets as poradca_tickets
from backend.services.poradca import tools
from backend.tests.fake_plane import FakePlane, gateway_for

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    fake.modules.append({"id": "m-nsv", "name": "NEX Studio Visual"})
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    for issue_id, name, state in (
        ("x372", "Šablóna projektov menuje ľudí — meno v zdieľanej šablóne", "s-done"),
        ("x380", "V šablóne projektov chýba meno súboru README", "s-todo"),
        ("x381", "Poradca nevidí náklady verzie", "s-todo"),
    ):
        fake.issues[issue_id] = {
            "id": issue_id,
            "sequence_id": int(issue_id.removeprefix("x")),
            "name": name,
            "description_html": "<p>Robí: Andros Dedo</p>",
            "state": state,
            "labels": [],
            "assignees": [],
            "updated_at": "2026-11-13T13:41:00Z",
            "deleted_at": None,
            "archived_at": None,
        }
        fake.module_issues.setdefault("m-nsv", []).append(issue_id)
    return fake


def _poradca() -> tools.PoradcaTools:
    return tools.PoradcaTools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())


async def test_poradca_finds_the_closed_ticket_it_was_about_to_remind(plane):
    """The case of 13.11.2026: the ticket on the template's names is closed — Poradca reads its number and state."""
    answer = await _poradca().kokpit_tikety({"nazov": "meno v šablóne projektov"})

    lines = answer.splitlines()
    assert lines[0] == "Podobné tikety kokpitu (otvorené aj uzavreté), najpodobnejší prvý:"
    # both share all three words with the question; among equals the newer comes first (DEV-388)
    assert [line.split(" ", 2)[:2] for line in lines[1:]] == [["DEV-380", "[Todo]"], ["DEV-372", "[Done]"]]
    assert not any("DEV-381" in line for line in lines), "a ticket on another matter is no match"


async def test_nothing_similar_is_said_plainly(plane):
    assert await _poradca().kokpit_tikety({"nazov": "faktúra zákazníka v eurách"}) == (
        "V tiketoch kokpitu nie je podobný tiket — ani otvorený, ani uzavretý."
    )


def test_the_preview_keeps_showing_the_open_ones_only(plane):
    """One search, two askers: the „Založiť tiket“ preview is unchanged — a closed ticket is no duplicate to found."""
    gateway = plane_tickets.gateway()
    module = gateway.find_module(None, poradca_tickets.COCKPIT_MODULE)

    preview = poradca_tickets.similar_tickets(gateway, module, "meno v šablóne projektov")
    asked = poradca_tickets.cockpit_similar(gateway, "meno v šablóne projektov")

    assert [t.identifier for t in preview] == ["DEV-380"]
    assert {t.identifier for t in asked} == {"DEV-372", "DEV-380"}


def test_poradca_is_given_the_tool_and_told_to_use_it():
    names = {tool.name for tool in tools.build_tools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())}
    charter = CHARTER.read_text(encoding="utf-8")

    assert "kokpit_tikety" in names
    assert "ty ich nevidíš" not in charter
    assert "Kým tiket kokpitu navrhneš\n    alebo pripomenieš, pozri nástrojom `kokpit_tikety`" in charter
    assert "| `kokpit_tikety` |" in charter
