"""DEV-356 — what a person passes on from an answer is Slovak: an English block goes back to its agent, never offered.

Director 11.11.2026: „Prekontroluj čo sa udialo, Poradca v NEX Knowledge pokyn pre Staviteľa napísal po anglicky.“ The
Poradca answered in Slovak and wrote the instruction for the Staviteľ in English — 1 of its 183 instructions; the
finding it answered was about translating code into English. Its charter asked for a Slovak answer and never said so of
the blocks, and the cockpit offered whatever a block held. Director 09.10.2026: „celá komunikácia s Agentom ide po
slovensky“. Now both charters say it, the answer goes back once to be rewritten, and what is still not Slovak is offered
nowhere — not under the answer, not by a click.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.models.poradca import PoradcaMessage
from backend.services.poradca import block_language, handoff, ideas, runner
from tests.test_ideas_talk_without_a_project import ADDITION, BRIEF
from tests.test_poradca_proposes_a_ticket_and_a_click_founds_it import _answer, plane, setup  # noqa: F401
from tests.test_poradca_runner import _capture_calls, _finish, world  # noqa: F401

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

#: The instruction of 11.11.2026, as the Poradca of NEX Knowledge wrote it.
ENGLISH_INSTRUCTION = (
    "<pokyn-pre-agenta>\n"
    "Two precisions to the Auditor's findings:\n1. frontend/src/preview/fixtures.ts:133–135 — rewrite the "
    "whole comment in English and refer to the areas by their ids (a-mgmt, a-legal, a-econ, a-hr) and to "
    'the level as "the strict level", not by their Slovak names; lines 134–135 contain Slovak names too, '
    "not only line 133. Do not change any displayed text (the area(...) arguments stay as they are).\n2. "
    "frontend/src/types/knowledge.ts:467 and :484 — ShareBlockReason and AppReadKind are used inside the "
    "same file (AppShares.blocked.reason, AppRead.kind), only their export is unused. Remove just the "
    "`export` keyword, do not delete the types and do not add them to docs/dead-code-exceptions.yml.\n"
    "</pokyn-pre-agenta>"
)

#: The same instruction in Slovak — paths, names from code and a quoted text stay as they are.
SLOVAK_INSTRUCTION = """<pokyn-pre-agenta>
Dve spresnenia k nálezom Audítora:
1. `frontend/src/preview/fixtures.ts:133–135` — celý komentár prepíš po anglicky a oblasti menuj ich identifikátormi \
(`a-mgmt`, `a-legal`, `a-econ`, `a-hr`), stupeň ako "the strict level". Slovenské mená sú aj na riadkoch 134–135. \
Text, ktorý aplikácia zobrazí (argumenty `area(...)`), nemeň.
2. `frontend/src/types/knowledge.ts:467` a `:484` — ShareBlockReason a AppReadKind sa v súbore používajú, zbytočné je \
len slovo `export`. Odstráň len ho, typy nemaž a do docs/dead-code-exceptions.yml ich nepridávaj.
</pokyn-pre-agenta>"""

ENGLISH_REQUIREMENT = (
    "<poziadavka-do-zasobnika>\nExport to Excel\nThe invoices should be exported to Excel with all their lines and the "
    "totals of each month.\n</poziadavka-do-zasobnika>"
)
ENGLISH_CHOICE = (
    "Odporúčam toto.\n\n<volba>Tell the agent that it should write the model into the log as well, and keep the old "
    "entries as they are.</volba>"
)
ENGLISH_BRIEF = BRIEF.replace(
    "Farnosť vedie matriku na papieri a chce ju mať v aplikácii.",
    "The parish keeps its register on paper and wants to have it in the application.",
)
ENGLISH_ADDITION = ADDITION.replace(
    "Správca vytvorí skupinu pre každú farnosť a pridelí jej ľudí.",
    "The administrator creates a group for each parish and assigns its people to it.",
)


def _messages(client, conversation):
    return client.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["messages"]


# ── the reading ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_english_instruction_of_11_11_is_found_and_its_slovak_twin_with_code_is_not():
    found = block_language.not_in_slovak(f"Nálezy sú oprávnené.\n\n{ENGLISH_INSTRUCTION}")

    assert [(f.block, f.label) for f in found] == [("pokyn-pre-agenta", "pokyn pre agenta")]
    assert any(s.startswith("Do not change any displayed text") for s in found[0].sentences)
    assert block_language.not_in_slovak(f"Nálezy sú oprávnené.\n\n{SLOVAK_INSTRUCTION}") == []


def test_every_block_a_person_passes_on_is_read_also_with_its_attributes():
    for answer, block in (
        (ENGLISH_REQUIREMENT, "poziadavka-do-zasobnika"),
        (ENGLISH_CHOICE, "volba"),
        (ENGLISH_CHOICE.replace("<volba>", '<volba karta="1">'), "volba"),
        (ENGLISH_BRIEF, "zadanie-projektu"),
        (ENGLISH_ADDITION, "doplnenie-projektu"),  # <doplnenie-projektu projekt="nex-manager">
    ):
        assert [f.block for f in block_language.not_in_slovak(answer)] == [block], block
    for slovak in (BRIEF, ADDITION, "<volba>Áno, súhlasím</volba>", '<volba karta="2">Nechaj to opraviť</volba>'):
        assert block_language.not_in_slovak(slovak) == [], slovak


def test_english_outside_the_blocks_is_not_their_business():
    """The answer itself is the charter's business — only what the person passes on is checked."""
    assert block_language.not_in_slovak(f"The answer is here.\n\n{SLOVAK_INSTRUCTION}") == []


# ── offered nowhere ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_an_answer_with_an_english_block_offers_nothing_and_says_why(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, f"Nálezy sú oprávnené.\n\n{ENGLISH_INSTRUCTION}\n\n{ENGLISH_REQUIREMENT}")

    shown = _messages(client, conversation)[-1]

    assert shown["instruction"] is None and shown["backlog_request"] is None
    assert shown["not_in_slovak"] == (
        "Kokpit neponúka, čo nie je po slovensky: pokyn pre agenta, požiadavka do Zásobníka. "
        "Požiadaj o prepísanie po slovensky — cesty a mená z kódu ostanú, ako sú."
    )


def test_an_answer_in_slovak_offers_its_instruction_and_says_nothing(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, f"Nálezy sú oprávnené.\n\n{SLOVAK_INSTRUCTION}")

    shown = _messages(client, conversation)[-1]

    assert shown["instruction"].startswith("Dve spresnenia k nálezom Audítora:")
    assert shown["not_in_slovak"] is None


def test_the_click_on_an_english_requirement_is_refused_with_the_sentence(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    message = _answer(db_session, conversation, f"Ulož to.\n\n{ENGLISH_REQUIREMENT}")

    resp = client.post(f"/api/v1/poradca/messages/{message.id}/backlog")

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Kokpit neponúka, čo nie je po slovensky: požiadavka do Zásobníka.")


def test_an_english_brief_is_no_outcome_of_the_idea():
    def answer(content: str) -> PoradcaMessage:
        return PoradcaMessage(author="poradca", status="done", content=content)

    assert ideas.offers_outcome(answer(BRIEF)) and ideas.offers_outcome(answer(ADDITION))
    assert not ideas.offers_outcome(answer(ENGLISH_BRIEF))
    assert not ideas.offers_outcome(answer(ENGLISH_ADDITION))
    assert handoff.withheld(ENGLISH_BRIEF).startswith("Kokpit neponúka, čo nie je po slovensky: zadanie projektu.")


# ── back to the agent ───────────────────────────────────────────────────────────────────────────────────────────────


def _scripted(monkeypatch, *answers: str) -> list[str]:
    """Each Claude call of the turn answers the next text; what the calls were asked is in ``_capture_calls``."""
    left = list(answers)

    async def _stream(argv, entry, message_id, project_dir):
        return left.pop(0), None

    monkeypatch.setattr(runner, "_stream", _stream)
    return left


async def test_an_english_instruction_goes_back_once_in_the_same_session_and_the_slovak_one_is_kept(
    world,  # noqa: F811
    monkeypatch,
):
    calls = _capture_calls(monkeypatch)
    english, slovak = f"Nálezy sú oprávnené.\n\n{ENGLISH_INSTRUCTION}", f"Opravené.\n\n{SLOVAK_INSTRUCTION}"
    left = _scripted(monkeypatch, english, slovak)
    db = world["db"]

    _, answer = runner.ask(db, world["conversation"], "Je nález opodstatnený?", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)

    assert stored.status == "done", stored.error
    assert stored.content == f"Opravené.\n\n{SLOVAK_INSTRUCTION}" and left == []
    assert len(calls) == 2
    first, rewrite = calls
    assert rewrite.claude_session_id == first.claude_session_id  # the same session — it knows its own answer
    assert first.charter_text == "charta" and rewrite.charter_text is None  # the first starts it, the rewrite resumes
    assert "pokyn pre agenta (`<pokyn-pre-agenta>`)" in rewrite.prompt
    assert "Do not change any displayed text" in rewrite.prompt
    assert {"tool": runner.REWRITE_STEP, "target": "pokyn pre agenta"} in stored.steps


async def test_a_slovak_answer_is_asked_once(world, monkeypatch):  # noqa: F811
    calls = _capture_calls(monkeypatch)
    _scripted(monkeypatch, f"Nálezy sú oprávnené.\n\n{SLOVAK_INSTRUCTION}")
    db = world["db"]

    _, answer = runner.ask(db, world["conversation"], "Je nález opodstatnený?", world["user"])
    await _finish(answer.id)
    db.expire_all()

    assert db.get(PoradcaMessage, answer.id).status == "done"
    assert len(calls) == 1


async def test_an_answer_still_english_after_the_rewrite_is_kept_and_offers_nothing(world, monkeypatch):  # noqa: F811
    calls = _capture_calls(monkeypatch)
    _scripted(monkeypatch, ENGLISH_INSTRUCTION, ENGLISH_INSTRUCTION)
    db = world["db"]

    _, answer = runner.ask(db, world["conversation"], "Je nález opodstatnený?", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)

    assert stored.status == "done" and len(calls) == 2  # once back, not in a loop
    assert handoff.withheld(stored.content).startswith("Kokpit neponúka, čo nie je po slovensky: pokyn pre agenta.")


# ── the charters say it ─────────────────────────────────────────────────────────────────────────────────────────────


def test_both_charters_say_what_is_passed_on_is_slovak():
    poradca = " ".join((TEMPLATES / "poradca-charter.md").read_text(encoding="utf-8").split())
    projektant = " ".join((TEMPLATES / "poradca-ideas-charter.md").read_text(encoding="utf-8").split())

    assert "**Čo človek odovzdá ďalej, píšeš po slovensky** — pokyn pre agenta, odpoveď pre agenta" in poradca
    assert "aj keď sa vec týka angličtiny (napríklad prekladu kódu)" in poradca
    assert "**Zadanie aj doplnenie píšeš po slovensky**" in projektant
    for text in (poradca, projektant):
        assert "Blok, ktorý nie je po slovensky, ti kokpit vráti na prepísanie a človeku ho neponúkne." in text
