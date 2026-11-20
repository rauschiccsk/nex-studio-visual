"""DEV-442 — Poradca marks an answer that asks the Manažér's consent, and the cockpit offers „Súhlasím“ under it.

Director 20.11.2026: „chcel by som mať také isté tlačidlo aj v Poradca, avšak s tým rozdielom, že toto tlačidlo sa
objaví ak Poradca pýta odo mňa súhlas.“ The cockpit does not guess consent from the words; the charter tells Poradca to
mark it, and the screen looks for the very same mark (frontend ``lib/poradcaAnswer.ts``).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARTER = ROOT / "templates" / "poradca-charter.md"
SCREEN = ROOT / "frontend" / "src" / "lib" / "poradcaAnswer.ts"


def test_the_charter_tells_poradca_to_mark_a_question_for_consent():
    text = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert (
        "Keď žiadaš súhlas Manažéra s odporúčaním (DEV-442), daj na posledný riadok odpovede značku `<suhlas/>`."
        in text
    )
    assert "Pri vysvetlení, náleze či otázke, na ktorú „Súhlasím“ nie je odpoveď, značku nedávaj." in text


def test_the_screen_looks_for_the_mark_the_charter_names():
    pattern = re.search(r"const CONSENT_RE = /(.+)/g;", SCREEN.read_text(encoding="utf-8"))

    assert pattern is not None
    assert re.fullmatch(pattern.group(1).replace("\\/", "/"), "<suhlas/>")
