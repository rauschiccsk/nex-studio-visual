"""DEV-517 — every app the cockpit builds has the back link to the previous screen and tight table rows from the start:
the Staviteľ's charter makes them the ICC standard and points to the template's KitPage, KitTable and KitFields.

NEX DualBook 0.1.0, Vizuál 26.11.2026: the Director had to ask for tight rows twice and for „← previous screen“ above
every screen — „Chcem aby tieto nastavenia boli založené aj pre všetky nové projekty a ne riešiť ich osobitne.“
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"


def test_the_stavitel_builds_every_screen_with_the_back_link_and_tight_rows() -> None:
    charter = CHARTER.read_text(encoding="utf-8")
    start = charter.index("**Šípka späť a tesné riadky — štandard ICC (DEV-517).**")
    rule = charter[start : charter.index("\n- **", start + 1)]

    for said in (
        "← názov predchádzajúcej obrazovky",
        "s filtrom a záložkou",
        "položka ľavého panela začína novú cestu",
    ):
        assert said in rule
    for part in ("`KitPage`", "`KitTable`", "`KitFields`", "v0.20.0"):
        assert part in rule
    assert "Manažér to nemá vypytovať vo" in rule
