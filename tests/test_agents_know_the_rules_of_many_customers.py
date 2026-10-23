"""DEV-137 / DEV-189 — every build agent knows the rules of one application for many customers.

Director 16.10.2026: „…vidím nebezpečenstvo, že ak opravíme niečo pre jedného zákazníka pokazíme niečo u existujúceho.“
23.10.2026 to the plan: „Súhlasím, schvaľujem plán DEV-137“. The rules — a document is read by the common code alike
for all (P2), a customer differs by the settings of his installation (P3), every real document fixed on stays in the
project's tests (P4) — reach every project as the cockpit's skill ``multi-customer``, and both the AI Agent, who
applies them, and the Auditor, who checks them, are told where it is.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import create_project_postscaffold as cps

SKILL = "multi-customer"
NAMED = re.compile(r"zručnos\w*\s+`multi-customer`")


def test_a_new_project_gets_the_rules_and_both_agents_are_told_where_they_are(tmp_path: Path):
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False, project_type="standard")

    skill = (root / ".claude" / "skills" / SKILL / cps.SKILL_FILE).read_text(encoding="utf-8")
    assert [rule for rule in ("(P2)", "(P3)", "(P4)") if rule in skill] == ["(P2)", "(P3)", "(P4)"]
    assert "`private/`" in skill  # the original of a customer's document never goes into git
    for role in ("ai-agent", "auditor"):
        charter = (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8")
        assert NAMED.search(charter), f"{role}: the charter does not name the skill {SKILL}"
