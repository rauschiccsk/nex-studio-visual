"""DEV-433 — the Staviteľ knows that a database image changed in compose reaches him in the next turn, not this one.

18.11.2026, Orthodox Hermenia 0.1.0, task #1.2.1: the Staviteľ moved the project's ``db`` service to
``pgvector/pgvector:pg16`` (commit 2fa0887) and his test ``test_the_database_offers_pgvector`` failed in the same turn —
the turn's database was still the ``postgres:16-alpine`` the compose named when the turn started. He escalated it as a
NEX Studio bug (``framework_issue``) and the build stood waiting for Dedo, with nothing in the cockpit to repair: the
engine plans the database of every Programovanie turn afresh from the compose as it is then
(``build_sandbox.plan_turn_database`` → ``build_db.plan_database``), so the next turn already got pgvector. His charter
said the database is given by the engine and dies with the turn — not that its image is read at the turn's start.
Director, on adding the sentence: „Súhlasím“; on doing it: „Sprav z DEV-433 balík a implementuj“.

The sentence is checked where the Staviteľ reads it (his charter as the cockpit writes it into a project), and the
behaviour it promises is checked against the code that decides it.
"""

from __future__ import annotations

from backend.services import build_db

SENTENCE = "Zmena obrazu databázy v `docker-compose.yml` platí od ďalšieho ťahu"


def test_the_staviteľ_reads_that_a_new_database_image_comes_with_the_next_turn(tmp_path):
    from backend.services import create_project_postscaffold as cps

    root = tmp_path / "acme"
    (root / ".claude").mkdir(parents=True)

    cps.refresh_v2_agent_charters(root, "acme")
    charter = (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8")

    assert SENTENCE in charter
    paragraph = charter[charter.index(SENTENCE) :].split("\n  - ", 1)[0]
    assert "nie je to `framework_issue`" in paragraph


def test_the_database_of_a_turn_is_read_from_the_compose_as_it_is_when_the_turn_starts(tmp_path):
    """What the sentence promises: the same project, its compose changed between two turns, gets the new image."""
    root = tmp_path / "acme"
    root.mkdir()
    compose = root / "docker-compose.yml"

    compose.write_text("services:\n  db:\n    image: postgres:16-alpine\n", encoding="utf-8")
    this_turn = build_db.plan_database(slug="acme", host_project_dir=str(root), token="t" * 32)
    compose.write_text("services:\n  db:\n    image: pgvector/pgvector:pg16\n", encoding="utf-8")
    next_turn = build_db.plan_database(slug="acme", host_project_dir=str(root), token="t" * 32)

    assert (this_turn.image, next_turn.image) == ("postgres:16-alpine", "pgvector/pgvector:pg16")
