"""DEV-346 — the Projektant knows an idea ends once, and offers the change of another project as an idea of its own.

10.11.2026 in the idea „FINANČNÝ MODUL - MAGER“ the Projektant wrote the brief of NEX Finance and promised: „Doplnenie
NEX Inboxu pripravím v ďalšej odpovedi ako samostatný blok.“ The cockpit ends an idea once — „Založiť projekt“ or
„Pridať do projektu“ — and offers it under the latest brief only: that addition would have become the latest brief, the
button to found NEX Finance would have gone, and after „Pridať do projektu“ NEX Finance could not be founded from the
idea at all. Its rules described both endings but not that there is only one. Director: „Založ a hneď aj urob.“

So the rule stands in the Projektant's rules, and these tests hold the cockpit to what the rule says about it — when
the cockpit learns to end an idea twice, they turn red and the rule is rewritten with it.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.models.projects import Project
from backend.services.poradca import ideas
from tests.test_ideas_end_in_a_project import PAID_BRIEF, _client, _idea_with, _new_project, world  # noqa: F401
from tests.test_ideas_talk_without_a_project import ADDITION, answers  # noqa: F401 — fixture of world

RULES = Path(__file__).resolve().parents[1] / "templates" / "poradca-ideas-charter.md"


def test_the_projektants_rules_say_an_idea_ends_once():
    rules = " ".join(RULES.read_text(encoding="utf-8").split())
    assert "Nápad končí len raz" in rules
    assert "len pod posledným zadaním" in rules
    assert "samostatný nový nápad" in rules
    assert "docs/specs/versions/v0.1.0/customer-requirements.md" in rules
    assert "Nesľubuj" in rules and "v tomto nápade nedokáže" in rules


def test_an_idea_founded_cannot_also_be_added_to_another_project(world):  # noqa: F811
    db = world["db"]
    idea, (brief, addition) = _idea_with(world, PAID_BRIEF, ADDITION)
    project = _new_project(db, world["me"])
    manager = Project(
        name="NEX Manager", slug="nex-manager", type="standard", auth_mode="password", description="d",
        created_by=world["me"].id,
    )  # fmt: skip
    db.add(manager)
    db.commit()  # a refused click rolls its request back — the projects stand before the first one
    c = _client(db, world["me"])

    founded = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": brief, "project_id": str(project.id)}
    )
    added = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": addition, "project_id": str(manager.id)}
    )

    assert founded.status_code == 409 and founded.json()["detail"] == ideas.REWRITTEN, (
        "a later addition replaces the brief"
    )
    assert added.status_code == 200, added.text
    again = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": brief, "project_id": str(project.id)}
    )
    assert again.status_code == 409, "an idea that ended once does not end a second time"


def test_the_rules_name_the_follow_up_button_as_the_screen_shows_it():
    """DEV-350 — the Projektant tells the person which button opens the follow-up idea; the name it says is the name
    on the screen, read from both, so neither can drift away from the other."""
    screen = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "IdeasPage.tsx").read_text(
        encoding="utf-8"
    )
    label = "Nový nápad nadväzujúci na tento projekt"
    assert label in screen
    rules = " ".join(RULES.read_text(encoding="utf-8").split())
    assert f"„{label}“" in rules
    assert "„Pridať do projektu“ funguje pre **ktorýkoľvek projekt kokpitu**" in rules
