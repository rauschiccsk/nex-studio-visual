"""DEV-518 — every app the cockpit builds sizes a field by what goes into it and keeps a checkbox by its label: the
Staviteľ's charter makes it the ICC standard and points to nex-shared v0.41.0, whose Input and Select carry it.

NEX HelpDesk, first setup 26.11.2026: the time „08:00“ spanned the whole screen and the second time was past its edge.
The Director: „Chcel by som aby dlžka editorov bolo rozumne prispôsobené k údajom, čo zadávam.“
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"


def test_the_stavitel_sizes_every_field_by_its_data() -> None:
    charter = CHARTER.read_text(encoding="utf-8")
    start = charter.index("**Šírka poľa podľa údaja — štandard ICC (DEV-518).**")
    rule = " ".join(charter[start : charter.index("\n- **", start + 1)].split())

    for said in (
        "čas úzky",
        "celú šírku dostane len dlhý text",
        "výber je široký ako jeho možnosti, na telefóne vyplní svoje miesto",
        "Zaškrtávacie políčko stojí hneď pri svojom popise",
    ):
        assert said in rule
    for part in ("`nex-shared` v0.41.2", '`width="full"`', "`w-full`"):
        assert part in rule
    assert "Manažér to nemá vypytovať vo Vizuáli" in rule
