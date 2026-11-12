"""DEV-369 — the documentation of a project names roles, never people.

Director 12.11.2026: „Chcel by som aby naša dokumentácia bola profesionálná, a do budúcna nemá obsahovať mená
pracovníkov.“ On the proposal — „súhlasím“. The Poradca's and the Projektant's charters named the people of the
cockpit („Manažérom (Zoltán, Tibor, Nazar)“), no agent had a rule that a document names roles, and the Auditor never
looked: names of the cockpit's people stood in the documentation of 17 projects, three times in the Špecifikácia of
NEX Finance 0.1.0. Now the charters speak of roles, every agent writes roles, and the Auditor takes a name from the
cockpit's accounts in the version's documentation for a gap — the names come from the accounts, not from a list.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from backend.db.models.foundation import User
from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import _make_version

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

#: The people with an account in the cockpit on 12.11.2026 — what the charters must not name.
TEAM = ("Zoltán", "Tibor", "Nazar", "Dominik", "Tatiana", "Rausch", "Teliuk", "Jenik")

RULE = "nemenuj ľudí — len roly (Manažér, strana NEX Genesis, zákazník, vedenie zákazníka)"


def _flat(name: str) -> str:
    return " ".join((TEMPLATES / name).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("charter", ["poradca-charter.md", "poradca-ideas-charter.md", "ai-agent-charter.md"])
def test_no_charter_names_the_cockpits_people(charter):
    text = _flat(charter)
    assert [n for n in TEAM if re.search(rf"(?<!\w){n}\w*", text)] == []


@pytest.mark.parametrize("charter", ["poradca-charter.md", "poradca-ideas-charter.md", "ai-agent-charter.md"])
def test_every_agent_writes_roles(charter):
    assert RULE in _flat(charter)


def _person(db, first, last=None):
    db.add(
        User(
            username=f"u_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            role="ri",
            first_name=first,
            last_name=last,
        )
    )
    db.flush()


@pytest.fixture
def version(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, _ = _make_version(db_session, project_dial="plna")
    _person(db_session, "Eva", "Testová")
    _person(db_session, "Administrátor")
    _person(db_session, "Develop", "Rausch")
    _person(db_session, "Al", "Bo")
    return version


def test_the_names_are_the_accounts_without_role_words_and_short_ones(db_session, version):
    names = orchestrator.people_names(db_session)

    assert {"Eva", "Testová", "Rausch"} <= set(names)
    assert not {"Administrátor", "Develop", "Al", "Bo"} & set(names)


@pytest.mark.parametrize("brief", ["navrh_review", "verifikacia"])
def test_the_auditor_takes_a_name_in_the_documentation_for_a_gap(db_session, version, brief):
    text = (
        orchestrator._auditor_upfront_directive(db_session, version.id)
        if brief == "navrh_review"
        else orchestrator._verifikacia_directive(db_session, version.id)
    )
    flat = " ".join(text.split())

    assert "MENÁ ĽUDÍ:" in flat
    assert "Výskyt mena v dokumentácii tejto verzie je MEDZERA (FAIL)" in flat
    listed = flat.split("Mená ľudí z kont kokpitu (aj skloňované): ", 1)[1].split(".", 1)[0]
    assert {"Eva", "Testová", "Rausch"} <= {n.strip() for n in listed.split(",")}
    assert "Administrátor" not in listed and "Develop" not in listed
