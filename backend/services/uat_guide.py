"""What to try in UAT — the test guide the AI Agent writes beside a version, and what the UAT tab shows (DEV-90).

12.10.2026 the Director: „V záložke UAT by som privýtal súhrn toho čo mám otestovať v novej verzii.“
The release notes say what changed, never how to try it, so he dug it out of the documentation.

The agent writes ``docs/specs/versions/v<N>/UAT_SKUSKA.md`` while it builds — every task that changes what a user
sees or does adds its steps (the rule is in each task's brief, :data:`BRIEF_RULE`, so a running build gets it too).
The verification stage checks the guide mechanically (:func:`problems`), like the English-code check: missing or without
steps, the version does not pass and the agent completes it in the fix. The UAT tab reads the guide of the commit
that was verified; a version from before the rule shows the changes of its release notes instead.

One shape, read by everybody — the brief shows it to the agent (:data:`BRIEF_RULE`), the check and the tab parse
it: a ``##`` heading per change as a user names it, then the screen (:data:`SCREEN_PREFIX`), optional test data
(:data:`DATA_PREFIX`), numbered steps and what has to happen (:data:`EXPECTED_PREFIX`). A version that changes
nothing a user can try says so in one line instead (:data:`NOTHING_PREFIX` and why).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.services.release_note_writer import version_notes_dir

FILE_NAME = "UAT_SKUSKA.md"
GIT_TIMEOUT_S = 15

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
STEP_RE = re.compile(r"^\d+[.)]\s+(.+?)\s*$")
SCREEN_PREFIX = "Obrazovka:"
DATA_PREFIX = "Testovacie údaje:"
EXPECTED_PREFIX = "Očakávané:"
NOTHING_PREFIX = "Nie je čo skúšať:"

#: The rule every Programovanie task brief carries (new version and fast fix alike). The path is filled in.
BRIEF_RULE = (
    "NÁVOD NA SKÚŠKU V UAT (DEV-90): Manažér skúša verziu v UAT podľa súboru `{path}`. Keď táto úloha mení to, čo "
    "používateľ vidí alebo robí, doplň do neho (založ ho, keď ešte nie je) zmenu presne v tomto tvare, po slovensky "
    "a bez technického žargónu:\n"
    "## <zmena tak, ako ju pomenuje používateľ>\n"
    "Obrazovka: <kde to nájde>\n"
    "Testovacie údaje: <čo zadať alebo použiť> (len keď treba)\n"
    "1. <krok — kam kliknúť, čo zadať; tlačidlá presne tak, ako sú na obrazovke>\n"
    "2. <ďalší krok>\n"
    "Očakávané: <čo sa má stať>\n"
    "Najdôležitejšie zmeny daj navrch. Keď úloha nemení nič, čo by sa dalo vyskúšať, súbor nemeň; keď celá verzia "
    "nemení nič viditeľné, napíš doň jediný riadok `Nie je čo skúšať: <prečo>`. Verifikácia návod skontroluje — "
    "bez neho verzia neprejde."
)


@dataclass
class Section:
    title: str
    screen: Optional[str] = None
    data: Optional[str] = None
    steps: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)


@dataclass
class Guide:
    sections: list[Section] = field(default_factory=list)
    #: The line (:data:`NOTHING_PREFIX`) saying the version changes nothing a user can try — and why.
    nothing_to_test: Optional[str] = None


def path_for(repo: Path, version_number: str) -> Path:
    return version_notes_dir(repo, version_number) / FILE_NAME


def relative_path(version_number: str) -> str:
    return path_for(Path("."), version_number).as_posix().removeprefix("./")


def parse(text: str) -> Guide:
    guide = Guide()
    current: Optional[Section] = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(NOTHING_PREFIX):
            guide.nothing_to_test = line[len(NOTHING_PREFIX) :].strip() or None
            continue
        heading = SECTION_RE.match(line)
        if heading:
            current = Section(title=heading.group(1))
            guide.sections.append(current)
            continue
        if current is None:
            continue
        step = STEP_RE.match(line)
        if step:
            current.steps.append(step.group(1))
        elif line.startswith(EXPECTED_PREFIX):
            current.expected.append(line[len(EXPECTED_PREFIX) :].strip())
        elif line.startswith(SCREEN_PREFIX):
            current.screen = line[len(SCREEN_PREFIX) :].strip() or None
        elif line.startswith(DATA_PREFIX):
            current.data = line[len(DATA_PREFIX) :].strip() or None
    return guide


def problems(text: Optional[str]) -> list[str]:
    """Why the guide is not good enough to test from — ``[]`` when it is. ``None`` = the file is missing."""
    if text is None:
        return ["chýba"]
    guide = parse(text)
    if guide.nothing_to_test and not guide.sections:
        return []
    if not guide.sections:
        return ["nemá ani jednu zmenu (## nadpis) ani riadok „Nie je čo skúšať: …“"]
    out = []
    for s in guide.sections:
        missing = [what for what, has in (("kroky", s.steps), ("„Očakávané: …“", s.expected)) if not has]
        if missing:
            out.append(f"zmena „{s.title}“ nemá {' ani '.join(missing)}")
    return out


def read_worktree(repo: Path, version_number: str) -> Optional[str]:
    """The guide as the working tree has it now — what the verification stage checks (no git, no process)."""
    path = path_for(repo, version_number)
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


def read_at(repo: Path, ref: str, rel_path: str) -> Optional[str]:
    """A file as commit ``ref`` has it; ``None`` when it is not there or git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{rel_path}"],
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


def release_note_changes(text: Optional[str]) -> list[str]:
    """The changes a release note lists (its bullets), as plain text — the fallback for a version without a guide."""
    out = []
    for line in (text or "").splitlines():
        m = _BULLET_RE.match(line)
        if m:
            out.append(re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(1)))
    return out


GUIDE = "guide"
RELEASE_NOTES = "release_notes"
NONE = "none"


@dataclass
class VersionGuide:
    """What the UAT tab shows for one version: its guide, or (older version) the changes of its release note."""

    source: str
    guide: Guide = field(default_factory=Guide)
    changes: list[str] = field(default_factory=list)


def for_commit(repo: Path, version_number: str, sha: Optional[str]) -> VersionGuide:
    """The guide of the commit that was verified (``sha``); its release note's changes when it has none.

    The note is read as the project has it NOW, and only then as the verified commit had it: a version's note is
    corrected after the version (DEV-97 — Career Asistent 0.1.1 shipped its agent brief as the note), and the
    corrected text is the one to test by. The guide stays the verified commit's — it describes that code."""
    if not sha:
        return VersionGuide(source=NONE)
    text = read_at(repo, sha, relative_path(version_number))
    if text is not None:
        return VersionGuide(source=GUIDE, guide=parse(text))
    note = (version_notes_dir(Path("."), version_number) / "RELEASE_NOTES.md").as_posix().removeprefix("./")
    now = read_at(repo, "HEAD", note)
    changes = release_note_changes(now if now is not None else read_at(repo, sha, note))
    return VersionGuide(source=RELEASE_NOTES if changes else NONE, changes=changes)


def admin_login(contract_settings: list, values: dict, legacy_admin: bool) -> Optional[str]:
    """How the customer's first admin signs in to UAT — the e-mail the manager entered, never a password.

    ``contract_settings`` are the app's customer settings (:mod:`env_contract`); the one naming an admin's e-mail
    carries the login. An app on the legacy ``ADMIN_INITIAL_PASSWORD`` signs in as ``admin``."""
    for setting in contract_settings:
        key = setting.key.upper()
        if "ADMIN" in key and "EMAIL" in key and values.get(setting.key):
            return str(values[setting.key])
    return "admin" if legacy_admin else None
