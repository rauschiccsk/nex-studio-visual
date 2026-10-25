"""DEV-224 — at an agent's question Poradca marks green only what to pick; a longer answer goes as an instruction.

25.10.2026 Poradca answered an agent's question with 2 505 characters between the choice tags, as the charter told it
(„Medzi značky preto daj celú odpoveď tak, ako ju má agent dostať“, DEV-135): the screen drew one big green box with a
small grey button and no purple button below. Director 26.10.2026: „zelené je to čo treba vybrať a dole má byť
viditeľný fialový button ako predtým“, „Áno, súhlasím, založ tiket a oprav to“. The screen half (a long choice drawn
as an instruction) is pinned in test_agent_question_goes_to_poradca.test.tsx.
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"


def _agent_question_rule() -> str:
    text = CHARTER.read_text(encoding="utf-8")
    start = text.index("**Keď agent čaká na odpoveď na svoju otázku**")
    return " ".join(text[start : text.index("\n## ", start)].split())


def test_a_longer_answer_for_the_agent_goes_as_an_instruction_not_as_a_choice():
    rule = _agent_question_rule()

    assert "zelená je len to, čo treba vybrať" in rule
    assert "**Dlhšiu odpoveď** — viac viet, body, pokyny — daj celú medzi značky `<pokyn-pre-agenta>`" in rule
    assert "**nie medzi značky voľby**" in rule
    assert "fialové tlačidlo „Vložiť do Riadiaceho centra“" in rule


def test_the_rule_that_put_the_whole_answer_between_the_choice_tags_is_gone():
    assert "Medzi značky preto daj **celú odpoveď" not in CHARTER.read_text(encoding="utf-8")
