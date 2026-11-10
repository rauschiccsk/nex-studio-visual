"""DEV-347 — the Projektant and the Poradca ask so that „Súhlasím“ is unambiguous: no tail „…, alebo …?“.

Director 10.11.2026: „Projektant stále ukončí otázku takto: "Súhlasíš s týmto rozsahom 0.1.0, alebo chceš niečo pridať
či ubrať?". … Ak napíšem "Súhlasím" nie je jednoznačné čím som súhlasil … Úplne postačujúce žiadať súhlas takto:
"Súhlasíš s týmto rozsahom 0.1.0?".“ Measured the same day: 5 of the Projektant's 8 closing questions in the idea
„FINANČNÝ MODUL - MAGER“ had the tail; the Poradca of a project 1 of 3. Both rules now say how a question is asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


@pytest.mark.parametrize("rules", ["poradca-ideas-charter.md", "poradca-charter.md"])
def test_the_rules_say_a_question_is_answered_by_yes(rules):
    text = " ".join((TEMPLATES / rules).read_text(encoding="utf-8").split())
    assert "aby „Súhlasím“ alebo „Áno“ bolo jednoznačné" in text
    assert "bez chvosta „…, alebo …?“" in text
    assert "Kto chce niečo iné, napíše to sám" in text
