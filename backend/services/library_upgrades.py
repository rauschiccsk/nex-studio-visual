"""DEV-354 — what an upgrade of the app template or nex-shared brought, told to the Príprava of the version.

10.11.2026, NEX Knowledge 1.1.0: the Manažér accepted the newer template (0.9.0 → 0.11.2) and nex-shared (0.29.0 →
0.35.0) when founding the version; the cockpit wrote a commit with the new numbers and nothing else. The first
Špecifikácia said „bez povýšenia“ and that the template's tables do not change, though the template's migration
kit_0002 drops the uniqueness of the e-mail; the release notes and the UAT guide did not mention the upgrade either. It
was put right only after the Manažér sent the Špecifikácia back. Director, on the ticket: „Súhlasím“; on doing it now:
„súhlasím“.

What the version brings is read where it is written: the pins of the frame (``frontend/package.json``) at the state
the previous finished version delivered (:func:`delivery_statement.resolve_base` — its sign-off, its PASS commit or
its tag) against the pins now, and the packages' change notes between the two releases. A first version, a version
without an upgrade, a project without the frame: nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from backend.services import app_kit, app_template, delivery_statement, nexshared

_PIN = re.compile(r"#v(\d+\.\d+\.\d+)$")
FRAME = "frontend/package.json"
#: The packages an upgrade at founding moves — how the brief names each, and where its change notes live.
PACKAGES: tuple[tuple[str, str, str], ...] = (
    (app_template.KIT_PACKAGE, "šablóna aplikácie nex-app-kit", app_kit.CHANGELOG_RAW_URL),
    (nexshared.PACKAGE, "nex-shared", nexshared.CHANGELOG_RAW_URL),
)
#: Characters of change notes per package the brief carries — the rest waits in the package's CHANGELOG.md.
CHANGES_CAP = 6000
HEADING = "POVÝŠENIE KNIŽNÍC PRI ZALOŽENÍ VERZIE"
#: When the reading did not finish in time — the agent is told where to look instead of hearing nothing.
UNKNOWN = (
    f"\n\n{HEADING}: či verzia nesie povýšenie šablóny aplikácie či nex-shared, kokpit nezistil. Pozri v gite commity "
    "„chore(deps): bump“ od poslednej verzie; ak nejaký je, Špecifikácia uvedie, čo z povýšenia verzia dotiahne."
)


@dataclass(frozen=True)
class Upgrade:
    package: str
    label: str
    old: str
    new: str
    changes: list[dict]


def _pins(package_json: Optional[str]) -> dict[str, str]:
    try:
        dependencies = (json.loads(package_json or "") or {}).get("dependencies") or {}
    except (ValueError, AttributeError):
        return {}
    pins = {}
    for name, _label, _url in PACKAGES:
        match = _PIN.search(str(dependencies.get(name) or ""))
        if match:
            pins[name] = match.group(1)
    return pins


def _at(repo: Path, commit: str, rel: str) -> Optional[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def upgrades(
    repo: Path, steps: list[delivery_statement.BaseStep], *, changelog: Optional[Callable[[str], str]] = None
) -> list[Upgrade]:
    """The packages whose pin rose since the previous finished version — with the change notes in between."""
    read_changes = changelog or nexshared.fetch_changelog
    base = delivery_statement.resolve_base(repo, steps, "HEAD")
    if base is None:
        return []
    before = _pins(_at(repo, base, FRAME))
    try:
        now = _pins((repo / FRAME).read_text(encoding="utf-8"))
    except OSError:
        return []
    found = []
    for name, label, url in PACKAGES:
        old, new = before.get(name), now.get(name)
        if old and new and nexshared._key(new) > nexshared._key(old):
            changes = nexshared.parse_changelog_sections(read_changes(url), old, new)
            found.append(Upgrade(name, label, old, new, changes))
    return found


def _changes(upgrade: Upgrade) -> str:
    if not upgrade.changes:
        return f"Zápisy zmien {upgrade.package} sa nepodarilo načítať — prečítaj CHANGELOG.md v jeho repozitári."
    text = "\n\n".join(f"### {upgrade.package} v{entry['version']}\n{entry['body']}" for entry in upgrade.changes)
    if len(text) > CHANGES_CAP:
        text = text[:CHANGES_CAP].rstrip() + f"\n… (skrátené — celý zápis je v CHANGELOG.md balíka {upgrade.package})"
    return text


def brief_block(found: list[Upgrade]) -> str:
    """What the Príprava brief says about the upgrade — nothing when there is none."""
    if not found:
        return ""
    moved = "\n".join(f"- {u.label}: z v{u.old} na v{u.new}" for u in found)
    notes = "\n\n".join(_changes(u) for u in found)
    return (
        f"\n\n{HEADING} (schválil ho Manažér):\n{moved}\n"
        "Špecifikácia musí uviesť, čo z povýšenia táto verzia dotiahne (nové pravidlá kontraktu šablóny, migrácie "
        "jej tabuliek, zmeny rozhrania) a čo sa zmení pre ľudí (poznámky k verzii, návod na skúšku v UAT). "
        "Nepíš, že verzia je bez povýšenia.\n"
        f"Zápisy zmien medzi verziami (CHANGELOG):\n\n{notes}"
    )


def brief_for(repo: Path, steps: list[delivery_statement.BaseStep]) -> str:
    return brief_block(upgrades(repo, steps))
