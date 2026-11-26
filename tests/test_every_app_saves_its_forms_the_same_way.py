"""DEV-520 — every app the cockpit builds saves a form the same way: Zrušiť and Uložiť active only on a change, never
„Uložené“ by the button, „✓ Uložené“ green at the top right. The Staviteľ's charter makes it the ICC standard and points
to nex-shared v0.42.0, whose SaveBar, announceSaved and AppShell carry it.

NEX HelpDesk, first setup 26.11.2026: „Uložené.“ stayed by the Save button after a change that was not saved. The
Director: „Chcel by som aby formuláre vo všetkých aplikáciách boli ošetrené rovnako.“
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"


def test_the_stavitel_saves_every_form_the_same_way() -> None:
    charter = CHARTER.read_text(encoding="utf-8")
    start = charter.index("**Ukladanie formulára — štandard ICC (DEV-520).**")
    rule = " ".join(charter[start : charter.index("\n- **", start + 1)].split())

    for said in (
        "obe sú aktívne len vtedy, keď sa vo formulári niečo zmenilo",
        "Zrušiť zahodí zmeny",
        "v okne ho zavrie",
        "Pri tlačidle nikdy nepíš „Uložené“",
        "„✓ Uložené“ zelenou vpravo hore",
    ):
        assert said in rule
    for part in ("`nex-shared` v0.42.0", "`SaveBar`", "`hasChanges`", "`announceSaved()`", "`AppShell`"):
        assert part in rule
    assert "Manažér to nemá vypytovať vo Vizuáli" in rule
