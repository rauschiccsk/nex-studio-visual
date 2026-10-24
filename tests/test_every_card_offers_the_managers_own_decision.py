"""DEV-220 — every Decision Card offers the Manažér's own decision, and it reaches the agent as his.

24.10.2026 Author Asistent 0.1.0: none of the options on a card suited the Director and the card would not take
his own answer — the screen showed „Iná odpoveď“ only where the agent allowed it (``allow_free_text``), and the
agent's rules allowed it by exception. Director: „na kartách otázok nie je možnosť zadať vlastné rozhodnutie …
mne nevyhovuje ani jeden. Túto možnosť treba doplniť do kokpitu.“

Pinned here (the screen half in ``test_decision_card_own_decision.test.tsx``): the screen and the server name the
choice alike; the agent reads an own decision as the Manažér's, not as one of its options; the agent no longer
writes an „iné“ option of its own; Poradca sees the choice on every card and an own decision as his.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import orchestrator
from backend.services.pipeline_status import OWN_DECISION_LABEL
from tests.test_orchestrator_v2_consultation import _seed_consultation
from tests.test_poradca_tools_db import _CARDS, _consultation_world, world  # noqa: F401 — the fixture

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "frontend" / "src" / "components" / "riadiace" / "DecisionCardsBar.tsx"
OWN_TEXT = "Aplikácia má jedného používateľa, ktorý smie všetko — rozdelenie právomocí zruš."


def test_the_screen_and_the_server_name_the_choice_alike() -> None:
    found = re.search(r'^const OWN_DECISION_LABEL = "([^"]+)";$', SCREEN.read_text(encoding="utf-8"), re.M)
    assert found, "the screen's OWN_DECISION_LABEL is gone or renamed — this guard must follow it"

    assert found.group(1) == OWN_DECISION_LABEL == "Vlastné rozhodnutie"


async def test_the_agent_reads_an_own_decision_as_the_managers_not_as_its_option(db_session):
    version = _seed_consultation(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "d1", "option_id": "a", "note": "Len pre nové záznamy."},
    )
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="decide", payload={"decision_key": "d2", "free_text": OWN_TEXT}
    )

    directive = orchestrator.dispatch_directive(db_session, version.id, "decide", {}, "navrh")

    assert directive is not None
    assert directive.splitlines()[1:3] == [
        "- Otázka 1? → Voľba A1 (poznámka: Len pre nové záznamy.)",
        f"- Otázka 2? → Vlastné rozhodnutie Manažéra (žiadna z ponúknutých možností): {OWN_TEXT}",
    ]


def test_the_agent_is_told_the_cockpit_adds_the_choice_and_writes_no_other_of_its_own(db_session):
    prompt = orchestrator._consultation_directive(
        db_session, None, source="auditor_upfront", findings=["nález"], proposed_fix=None
    )

    assert "možnosť typu „iné“ či „vlastná odpoveď“ NEPÍŠ — kokpit ku každej karte pridá „Vlastné rozhodnutie“" in (
        " ".join(prompt.split())
    )
    assert "allow_free_text" not in prompt


async def test_poradca_sees_the_choice_on_every_card_and_an_own_decision_as_his(world):  # noqa: F811
    _consultation_world(world, decided=False)
    db, version = world["db"], world["version"]
    await orchestrator.apply_action(
        db, version_id=version.id, action="decide", payload={"decision_key": "velke-faktury", "free_text": OWN_TEXT}
    )

    out = await world["tools"].stavba({})

    offered = "  možnosť „Vlastné rozhodnutie“ — Manažér napíše do poľa pod kartou, ako to má byť"
    assert out.splitlines().count(offered) == len(_CARDS["decisions"])
    assert f"  Manažér rozhodol sám („Vlastné rozhodnutie“): {OWN_TEXT}" in out.splitlines()
    assert f"Manažér zvolil „{OWN_TEXT}“" not in out
