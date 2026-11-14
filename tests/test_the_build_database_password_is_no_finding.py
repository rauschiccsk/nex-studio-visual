"""DEV-375 — the password of a build's temporary database is no finding: the agents know it lives for one turn.

13.11.2026, NEX Legal Watch 0.1.0: the Auditor kept recommending „overiť, že heslo dočasnej pracovnej databázy, ktoré
Staviteľ raz omylom vypísal do svojho výstupu, sa nepoužíva nikde inde“, and Poradca took it over („Priamo to overiť
neviem“). The engine draws that password at random for every Programovanie turn and removes the database after it
(``build_db.plan_database`` → ``secrets.token_hex``; ``claude_agent`` releases it in ``finally``; the freshness is
proven by ``tests/test_build_db.py::test_the_password_is_ours_fresh_every_turn_and_never_in_a_repr``) — but no charter
said so, so a printed one looked like a leak. Director, on the three fixes of Poradca's inaccuracies: „Súhlasím“; on
doing this one: „Áno začni“.

The sentence is checked where the agents read it — the Auditor's charter as the cockpit writes it into a project
(refreshed before every new session), Poradca's as its runner composes it.
"""

from __future__ import annotations

SENTENCE = "Heslo dočasnej databázy stavby nie je tajomstvo projektu"


def test_the_build_agents_read_it(tmp_path):
    """The Auditor judges the findings, so the sentence is his; the Staviteľ's own charter already says the database
    dies with the turn (the shared base would have pushed the Staviteľ over the ceiling of DEV-45 for a sentence he
    does not need)."""
    from backend.services import create_project_postscaffold as cps

    root = tmp_path / "acme"
    (root / ".claude").mkdir(parents=True)

    assert cps.refresh_v2_agent_charters(root, "acme") == 2
    charters = {
        role: (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8")
        for role in ("ai-agent", "auditor")
    }
    assert SENTENCE in charters["auditor"]
    assert "po ťahu zaniká" in charters["ai-agent"]


def test_poradca_reads_it():
    from backend.services.poradca import runner

    for project_type in ("standard", "web"):
        assert SENTENCE in runner._charter_text(project_type), project_type
