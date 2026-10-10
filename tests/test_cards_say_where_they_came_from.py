"""DEV-3 (ICCINT-122) — every new consultation card says where its question came from, every finding whether it
blocks — as DATA the screens can count on, not as hope.

Director 12.09.2026: „Ja chcem, aby manažér videl a bol informovaný, že práve skvalitňujeme a nerozbíjame to, čo
už bolo.“ The fields came on 14.09.2026, optional — and nobody filled them: the card looked like it worked and the
screens built on it (DEV-4, DEV-5) had nothing to show. Now the schema a NEW status block is written against
demands them, the validation refuses a card without its origin or a finding as a sentence (the agent is told why
and writes it again), and every instruction that asks for cards or findings says so. Records from before still read.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.services import pipeline_status as ps
from backend.services.pipeline_status import (
    PIPELINE_STATUS_JSON_SCHEMA,
    ConsultationBlock,
    ParseFailure,
    PipelineStatusBlock,
    _validate_block,
)

REPO = Path(__file__).resolve().parents[1]


def _card(key="telegram", **over) -> dict:
    return {
        "key": key,
        "question": "Ako posielať upozornenia?",
        "options": [
            {"id": "a", "label": "Telegram", "recommended": True},
            {"id": "b", "label": "E-mail"},
        ],
        "origin": "objav",
        **over,
    }


def _consultation(*cards: dict) -> dict:
    return {
        "stage": "navrh",
        "kind": "consultation",
        "summary": "Treba rozhodnúť.",
        "awaiting": "manazer",
        "consultation": {"id": "c1", "source": "auditor_upfront", "decisions": list(cards)},
    }


def _verdict(*findings) -> dict:
    return {
        "stage": "verifikacia",
        "kind": "verdict",
        "summary": "Verifikácia.",
        "awaiting": "manazer",
        "verdict": False,
        "findings": list(findings),
    }


# ── the schema a new block is written against ────────────────────────────────


def test_the_writers_schema_demands_the_origin_and_whether_a_finding_blocks():
    defs = PIPELINE_STATUS_JSON_SCHEMA["$defs"]
    assert "origin" in defs["ConsultDecision"]["required"]
    assert defs["ConsultDecision"]["properties"]["origin"]["enum"] == ["objav", "dosledok", "odklad"]
    assert "anyOf" not in defs["ConsultDecision"]["properties"]["origin"], "pôvod nesmie byť null"
    assert "blocking" in defs["Finding"]["required"]
    assert "default" not in defs["Finding"]["properties"]["blocking"]


# ── the validation of new output ─────────────────────────────────────────────


def test_a_card_without_its_origin_is_sent_back_naming_the_card():
    card = _card()
    del card["origin"]

    result = _validate_block(_consultation(card))

    assert isinstance(result, ParseFailure)
    assert result.reason == "consultation decision 'telegram' must carry 'origin' (one of objav, dosledok, odklad)"


def test_a_consequence_names_the_decision_it_follows_from():
    refused = _validate_block(_consultation(_card(origin="dosledok")))
    assert isinstance(refused, ParseFailure) and "'origin_of' must name the key" in refused.reason

    itself = _validate_block(_consultation(_card(origin="dosledok", origin_of="telegram")))
    assert isinstance(itself, ParseFailure) and "cannot follow from itself" in itself.reason

    ok = _validate_block(_consultation(_card(origin="dosledok", origin_of="topologia")))
    assert isinstance(ok, PipelineStatusBlock)
    assert (ok.consultation.decisions[0].origin, ok.consultation.decisions[0].origin_of) == ("dosledok", "topologia")


@pytest.mark.parametrize("origin", ["objav", "odklad"])
def test_a_card_with_its_origin_is_accepted(origin):
    assert isinstance(_validate_block(_consultation(_card(origin=origin))), PipelineStatusBlock)


def test_a_finding_written_as_a_sentence_is_sent_back():
    result = _validate_block(_verdict("Chýba strážca disku (neblokujúce)"))

    assert isinstance(result, ParseFailure)
    assert result.reason.startswith("findings: each finding must be an object {text, blocking}, not text")


def test_a_finding_that_does_not_say_whether_it_blocks_is_sent_back():
    result = _validate_block(_verdict({"text": "Chýba strážca disku"}))

    assert isinstance(result, ParseFailure)
    assert result.reason == "findings: finding 'Chýba strážca disku' must say 'blocking' (true/false)"


def test_findings_as_data_are_accepted_and_counted():
    block = _validate_block(
        _verdict(
            {"text": "Peniaze sa zaokrúhľujú po riadkoch", "blocking": True}, {"text": "Preklep", "blocking": False}
        )
    )

    assert isinstance(block, PipelineStatusBlock)
    assert [(f.text, f.blocking) for f in block.findings] == [
        ("Peniaze sa zaokrúhľujú po riadkoch", True),
        ("Preklep", False),
    ]


# ── records from before still read ───────────────────────────────────────────


def test_cards_and_findings_stored_before_still_read():
    old_card = _card()
    del old_card["origin"]
    stored = ConsultationBlock.model_validate({"id": "c0", "source": "auditor_upfront", "decisions": [old_card]})
    assert stored.decisions[0].origin is None

    old_verdict = PipelineStatusBlock.model_validate(_verdict("Chýba strážca disku", "Preklep v nadpise (neblokujúce)"))
    assert [f.blocking for f in old_verdict.findings] == [True, False], "starý záznam: závažnosť z vety"


# ── every card the cockpit makes itself, every instruction ───────────────────


def _calls(name: str):
    for path in sorted((REPO / "backend").rglob("*.py")):
        if "tests" in path.relative_to(REPO).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if func == name:
                    yield path.relative_to(REPO), node


def test_every_card_the_cockpit_makes_itself_says_where_it_came_from():
    calls = list(_calls("ConsultDecision"))
    assert calls, "stráž nenašla ani jednu kartu — hľadá zle"
    missing = [f"{p}:{n.lineno}" for p, n in calls if "origin" not in {k.arg for k in n.keywords}]
    assert missing == []


_FINDINGS_ASKED = re.compile(r"(?<!`)`findings`(?!`)")
_CARDS_ASKED = re.compile(r"(?<!`)`consultation\.decisions")


def test_every_instruction_that_asks_for_findings_or_cards_states_the_rule():
    """The rule, not a list: every statement of the orchestrator that builds text asking the agent for `findings`
    (or for consultation cards) carries the shared sentence in that same statement — one instruction function can
    hold two variants (Verifikácia), and a variant without the sentence must not hide behind its sibling."""
    tree = ast.parse((REPO / "backend" / "services" / "orchestrator.py").read_text(encoding="utf-8"))
    docstrings = {
        id(fn.body[0].value)
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
        and fn.body
        and isinstance(fn.body[0], ast.Expr)
        and isinstance(fn.body[0].value, ast.Constant)
    }
    asked: dict[str, int] = {"FINDINGS_AS_DATA_RULE": 0, "ORIGIN_RULE": 0}
    unstated: list[str] = []
    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.Return, ast.Assign, ast.AnnAssign, ast.AugAssign)) or stmt.value is None:
            continue
        texts = [
            n.value
            for n in ast.walk(stmt.value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]
        names = {n.id for n in ast.walk(stmt.value) if isinstance(n, ast.Name)}
        for rule, pattern in (("FINDINGS_AS_DATA_RULE", _FINDINGS_ASKED), ("ORIGIN_RULE", _CARDS_ASKED)):
            if any(pattern.search(t) for t in texts):
                asked[rule] += 1
                if rule not in names:
                    unstated.append(f"riadok {stmt.lineno} → {rule}")
    assert asked["FINDINGS_AS_DATA_RULE"] >= 4 and asked["ORIGIN_RULE"] >= 1, f"stráž nenašla pokyny: {asked}"
    assert unstated == []


def test_the_charters_say_it_too():
    agent = (REPO / "templates" / "ai-agent-charter.md").read_text(encoding="utf-8")
    auditor = (REPO / "templates" / "auditor-charter.md").read_text(encoding="utf-8")
    assert "svoj pôvod `origin`" in agent and "`origin_of`" in agent
    assert "Každý nález je objekt `{text, blocking}`" in auditor


def test_the_rules_name_every_origin():
    for origin in ps.CONSULT_ORIGINS:
        assert f"`{origin}`" in ps.ORIGIN_RULE
