"""DEV-519 — a field of the cockpit is as wide as what goes into it, and a checkbox stands by its label.

26.11.2026, Nastavenia → Používatelia: the select „Zodpovedný v Plane“ spanned from the middle of the screen to its
right edge, and at „Otázky Staviteľa najprv s Poradcom“ the name stood on the left and the checkbox far on the right.
The Director: „Aby som vedel nastaviť potreboval som pravítko. Chcel by som aby dlžka editorov bolo rozumne
prispôsobené k údajom, čo zadávam.“ The rule, read from every screen of the frontend, not from a list.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
SHORT_TYPES = ("time", "date", "number", "tel", "month", "datetime-local")


def _screens() -> list[tuple[Path, str]]:
    return [(f, f.read_text(encoding="utf-8")) for f in sorted(SRC.rglob("*.tsx")) if "__tests__" not in f.parts]


def _opening(text: str, start: int) -> str:
    """The opening tag of the element at ``start`` — attributes may hold arrows („=>“), so it is read to „/>“ or the
    first line that closes the tag."""
    chunk = text[start : start + 1200]
    end = min(i for i in (chunk.find("/>"), chunk.find("\n>"), chunk.find('">\n'), len(chunk)) if i >= 0)
    return chunk[: end + 2]


def _classes(opening: str) -> str:
    found = re.search(r'className=(?:\{`|")([^`"]*)', opening)
    return found.group(1) if found else ""


def _line(text: str, at: int) -> str:
    return str(text[:at].count("\n") + 1)


def _spans_its_place(classes: str) -> bool:
    """Full width with nothing narrower from a tablet up. On a phone a select fills its place — in a table cell only a
    width in per cent lets it shrink below its longest option (the e2e layout check at 375 px, 26.11.2026)."""
    return bool(re.search(r"(^|\s)w-full(\s|$)", classes)) and not re.search(r"(^|\s)sm:w-", classes)


def test_no_select_spans_its_whole_place() -> None:
    wide = [
        f"{path.relative_to(SRC)}:{_line(text, m.start())}"
        for path, text in _screens()
        for m in re.finditer(r"<select\b", text)
        if _spans_its_place(_classes(_opening(text, m.start())))
    ]
    assert wide == [], "výber roztiahnutý na celú šírku: " + ", ".join(wide)


def test_no_short_field_spans_its_whole_place() -> None:
    wide = []
    for path, text in _screens():
        for m in re.finditer(r"<input\b", text):
            opening = _opening(text, m.start())
            kind = re.search(r'type="([a-z-]+)"', opening)
            if kind and kind.group(1) in SHORT_TYPES and re.search(r"(^|\s)w-full(\s|$)", _classes(opening)):
                wide.append(f"{path.relative_to(SRC)}:{_line(text, m.start())} ({kind.group(1)})")
    assert wide == [], "krátke pole (čas, dátum, číslo) cez celú šírku: " + ", ".join(wide)


def test_a_checkbox_is_never_pushed_away_from_its_label() -> None:
    apart = []
    for path, text in _screens():
        for m in re.finditer(r'type="checkbox"', text):
            before = text[max(0, m.start() - 900) : m.start()]
            rows = [o for o in re.finditer(r"<(li|div|tr|label)\b[^>]*?className=\"([^\"]*)\"", before)]
            if any("justify-between" in o.group(2) for o in rows[-2:]):
                apart.append(f"{path.relative_to(SRC)}:{_line(text, m.start())}")
    assert apart == [], "zaškrtávacie políčko odtlačené od popisu (justify-between): " + ", ".join(apart)


def test_a_select_may_fill_its_place_only_on_a_phone() -> None:
    assert _spans_its_place("w-full rounded border")
    assert not _spans_its_place("w-full sm:w-auto sm:max-w-md rounded")
    assert not _spans_its_place("mt-1 max-w-full w-auto")


def test_the_guard_finds_what_it_guards() -> None:
    """A guard that cannot see a select at all would pass for nothing."""
    selects = sum(len(re.findall(r"<select\b", text)) for _path, text in _screens())
    checkboxes = sum(len(re.findall(r'type="checkbox"', text)) for _path, text in _screens())
    assert selects >= 10 and checkboxes >= 5
