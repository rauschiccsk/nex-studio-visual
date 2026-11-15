"""DEV-388 — the search for similar tickets meets a word in any of its Slovak forms.

15.11.2026, while verifying DEV-374 against the real Plane: Poradca's new tool, asked „meno v šablóne projektov“, did
not find DEV-372 „Spoločná šablóna projektov (Znalostná báza) menuje ľudí“ — the search compared whole words, and
„šablóne“ is not „šablóna“. Asked in the ticket's own words, it found it first. The „Založiť tiket“ preview runs the
same search. Director, on the ticket and on doing it: „založ tiket a hneď aj urob“.

The search compares the stem of each word — the word without its Slovak ending — and keeps its bar: two words in
common, all of a shorter title.
"""

from __future__ import annotations

import pytest

from backend.services import plane_tickets
from backend.services.poradca import tickets as poradca_tickets
from backend.tests.fake_plane import FakePlane, gateway_for

TEMPLATE_NAMES = "Spoločná šablóna projektov (Znalostná báza) menuje ľudí"


@pytest.fixture
def gateway(monkeypatch):
    fake = FakePlane()
    fake.modules.append({"id": "m-nsv", "name": "NEX Studio Visual"})
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    for issue_id, name, state in (
        ("x372", TEMPLATE_NAMES, "s-done"),
        ("x390", TEMPLATE_NAMES.replace("Spoločná", "Druhá"), "s-todo"),
        ("x391", "Šablóna zásobníka nemá popis", "s-todo"),
    ):
        fake.issues[issue_id] = {
            "id": issue_id,
            "sequence_id": int(issue_id.removeprefix("x")),
            "name": name,
            "description_html": "",
            "state": state,
            "labels": [],
            "assignees": [],
            "updated_at": "2026-11-15T09:00:00Z",
            "deleted_at": None,
            "archived_at": None,
        }
        fake.module_issues.setdefault("m-nsv", []).append(issue_id)
    return plane_tickets.gateway()


def test_a_word_in_another_form_still_meets_the_ticket(gateway):
    """The case of 15.11.2026 — „v šablóne projektov“ against „šablóna projektov“."""
    asked = poradca_tickets.cockpit_similar(gateway, "meno v šablóne projektov")

    assert {t.identifier for t in asked[:2]} == {"DEV-372", "DEV-390"}


def test_the_preview_meets_it_too(gateway):
    module = gateway.find_module(None, poradca_tickets.COCKPIT_MODULE)

    preview = poradca_tickets.similar_tickets(gateway, module, "v šablóne projektu menujú ľudia")

    assert [t.identifier for t in preview] == ["DEV-390"]  # open only; DEV-372 is done


def test_one_word_in_common_is_still_no_match(gateway):
    """The bar stays — „šablón“ alone does not make „Šablóna zásobníka nemá popis“ similar to a ticket on projects."""
    asked = poradca_tickets.cockpit_similar(gateway, "šablóny projektov menujú ľudí")

    assert "DEV-391" not in {t.identifier for t in asked}


@pytest.mark.parametrize(
    ("forms", "stem"),
    [
        (("šablóna", "šablóne", "šablónach", "šablónu", "šablóny"), "sablon"),
        (("projekt", "projektov", "projekty", "projektu", "projektom"), "projekt"),
        (("tiket", "tikety", "tiketov", "tiketu"), "tiket"),
        (("meno", "mena", "menom"), "men"),
        (("slovo", "slova", "slov", "slovom"), "slov"),  # „slov“ is no „sl“ + „-ov“: a stem keeps three letters
    ],
)
def test_the_forms_of_a_word_share_one_stem(forms, stem):
    assert {poradca_tickets._stem(poradca_tickets._plain(word)) for word in forms} == {stem}
