"""DEV-344 — the agent that builds a version in Riadiace centrum is the Staviteľ, never „AI Agent“.

Director 10.11.2026: „Potrebujem zrozumiteľne nazvať agenta, ktorý pracuje v riadiacom centre.“ — „AI Agent“ names every
agent of the cockpit; the work is the cockpit's „stavba“, so its agent is the Staviteľ, and the Audítor checks the
stavba. To the name: „Súhlasím, založ tiket a sprav.“ The rule is read from what exists — every sentence of the backend
and every rule an agent gets — not from a list of places that were renamed once.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
TEMPLATES = ROOT / "templates"

#: The old name in any form; „AI agentov“ / „AI agents“ are an app's own agents (a template feature), not ours.
OLD_NAME = re.compile(r"AI[ -]Agent|AI agent\b(?!ov|s)|\bagent(a|ovi)? stavby\b")


def _sentences(path: Path) -> list[str]:
    """Every string a module says — docstrings and comments are the code's own notes, not what a person reads."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def test_no_sentence_of_the_cockpit_calls_the_build_agent_ai_agent():
    said = {
        f"{path.relative_to(ROOT)}: {text[:100]!r}"
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.parts
        for text in _sentences(path)
        if OLD_NAME.search(text)
    }
    assert said == set()


def test_the_rule_sees_the_old_name_in_each_of_its_forms():
    """A pattern that matches nothing is green in a burning house."""
    for sentence in (
        "AI Agent pracuje",
        "otázka pre AI Agenta",
        "pošle sa AI Agentovi",
        "AI-Agent",
        "agent stavby stojí",
    ):
        assert OLD_NAME.search(sentence), sentence
    for sentence in ("Aplikácia používa AI agentov", "The app runs AI agents", "Staviteľ pracuje"):
        assert not OLD_NAME.search(sentence), sentence


def test_no_rule_an_agent_gets_calls_the_build_agent_ai_agent():
    said = {
        f"{path.name}:{number}"
        for path in TEMPLATES.rglob("*.md")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if OLD_NAME.search(line) and "nie „AI Agent“" not in line
    }
    assert said == set()


def test_the_stavitel_introduces_itself_so():
    rules = (TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8")
    assert rules.startswith("# Pravidlá agenta — Staviteľ")
    assert "Som **Staviteľ**" in rules and "V kokpite sa volám **Staviteľ**" in rules


def test_the_poradca_knows_every_agents_name():
    rules = (TEMPLATES / "poradca-charter.md").read_text(encoding="utf-8")
    for name in ("**Staviteľ**", "**Audítor**", "**Projektant**", "**Poradca**"):
        assert name in rules, name
