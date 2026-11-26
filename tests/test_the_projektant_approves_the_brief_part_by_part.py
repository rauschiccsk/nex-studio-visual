"""DEV-516 — the Projektant takes the final approval of a brief part by part: one part in one answer, the next only
after the person approved it, the whole brief with „Založiť projekt“ only when every part is approved.

25.11.2026, the idea NEX DualBook: the Projektant put the whole brief on the screen at once — dozens of pages. The
Director: „Do budúcna chcem aby jednotlivá časti návrhu A, B C ... Pred ponukou vytvorenia projektu boli po jednom
odsúhlasené a prekonzultované, pretože takto naraz spraviť všetko je dosť ťažké.“ It went part by part from B to H,
then „Schvaľuješ časti I, J a K?“ and „Schvaľuješ časti L a M?“ — the request lived in that one conversation only; a
new idea starts a new session and carries nothing but the charter. Director 26.11.2026: „…zadajme túto postupnosť
schválenia konečného návrhu.“
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-ideas-charter.md"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else None]


def test_the_brief_is_approved_one_part_at_a_time_before_it_is_offered() -> None:
    brief = _section(CHARTER.read_text(encoding="utf-8"), "## 5. Zadanie")

    assert "Záverečné schválenie po častiach" in brief
    assert "jednu časť v jednej odpovedi" in brief
    assert "nikdy dve časti naraz" in brief
    assert "Schvaľuješ časť" in brief
    assert "až keď sú schválené všetky časti" in brief


def test_the_whole_brief_is_no_longer_shown_at_once() -> None:
    assert "Zadanie ukáž človeku v bloku (časť 6)." not in CHARTER.read_text(encoding="utf-8")
