"""DEV-208 — the choice Poradca recommends is drawn once, where its answer names it, and Poradca names it first.

23.10.2026 an answer of Poradca showed the option to pick twice, one under the other: in the summary „Vyber …“ above
the answer and in its first sentence. Director: „zbytočne je vypísaná 2x čo treba vybrať (zelená) stačí len raz“.
The summary is gone; so that the choice is still seen at a glance in a long answer, the charter has Poradca say it in
the first sentence — and no longer promises a summary the cockpit does not draw.
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"


def test_the_charter_has_the_choice_said_in_the_first_sentence():
    charter = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert "povedz ju **hneď v prvej vete** odpovede — kokpit ju ukáže len tam, kde ju napíšeš" in charter
    assert "čísla karty, tiež hneď v prvej vete" in charter


def test_the_charter_promises_no_summary_above_the_answer():
    assert "nad odpoveďou" not in CHARTER.read_text(encoding="utf-8")
