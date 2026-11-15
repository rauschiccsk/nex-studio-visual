"""DEV-359 — how old each document of a project is, so an old summary is not taken for the project as it is now.

11.11.2026: Projektant wrote of NEX Inbox „AI cez Ollama na vlastnom serveri“. NEX Inbox runs on Claude through Claude
Code; the claim came from docs/specs/summary.md — a summary of 13.06.2026 for version 1.0.1 nobody updated since (NEX
Inbox was at 1.10.3). The list of documents Projektant reads (``dokumentacia_projektu``) gave 286 bare paths in
alphabetical order: the June summary looked as valid as the documents of 1.10.3. Director, on the ticket: „Áno
založ.“; on doing it now: „Súhlasím“.

Every document now comes with the date of its last change in the project's git (one ``git log`` for all of them), the
version a version's document belongs to, and a mark on what has not changed since the last finished version — Poradca
reads the same list (tool ``dokumenty``).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from backend.services import secret_files

DOCS_DIR = "docs"
ROOT_DOCS = ("README.md",)
#: How many documents the list names — the rest are counted.
DOCS_MAX = 300
_VERSION_DOC = re.compile(r"(?:^|/)versions/v(\d+\.\d+\.\d+)/")
#: What decides the project's state — the same sentence the charters of Projektant and Poradca carry.
RULE = (
    "O tom, čo projekt používa a ako funguje, rozhodujú dokumenty aktuálnej verzie a živé špecifikácie; starší "
    "dokument je história — keď sa naň odvolávaš, povedz, z ktorého je dátumu."
)
#: What the marks mean — a document outside the versions that did not change may still hold (verified live 15.11.2026:
#: in NEX Inbox 42 of its 43 such documents, the contract with NEX Genesis and the runbooks among them).
OLDER_VERSION = "dokument staršej verzie"
UNCHANGED = "v poslednej verzii sa nemenil"
MARKS = (
    f"„{OLDER_VERSION}“ je história. Dokument mimo verzií, ktorý „{UNCHANGED}“, môže stále platiť (schéma, návod) — "
    "keď z neho tvrdíš, čo projekt používa, over to v dokumentoch aktuálnej verzie a povedz jeho dátum."
)


def documents(root: str) -> list[str]:
    """The project's documentation: README.md and every .md under docs/, never a file of secrets."""
    found = [name for name in ROOT_DOCS if os.path.isfile(os.path.join(root, name))]
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, DOCS_DIR), followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md") and not secret_files.SECRET_FILE_RE.match(name):
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return found


def _git(root: str, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def last_changes(root: str) -> dict[str, str]:
    """Every document's last change (ISO date) in the project's git — one ``git log`` over docs/ and README.md."""
    out = _git(root, "log", "--format=@%cs", "--name-only", "--", DOCS_DIR, *ROOT_DOCS)
    changed: dict[str, str] = {}
    when = ""
    for line in (out or "").splitlines():
        if line.startswith("@"):
            when = line[1:]
        elif line.strip() and line not in changed:
            changed[line] = when
    return changed


def tag_date(root: str, version_number: str) -> Optional[str]:
    """When the version was tagged (ISO date) — the cockpit tags every verified version ``v<number>``."""
    out = _git(root, "log", "-1", "--format=%cs", f"refs/tags/v{version_number}")
    return out.strip() or None if out is not None else None


def _shown(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}.{month}.{year}"


def listing(root: str, slug: str, latest: Optional[str]) -> str:
    """The list of documents with their age — ``latest`` is the project's last finished version (or None)."""
    found = documents(root)
    if not found:
        return f"Projekt {slug} nemá dokumentáciu (docs/ ani README.md)."
    changed = last_changes(root)
    since = tag_date(root, latest) if latest else None
    lines = [
        f"Dokumentácia projektu {slug} ({len(found)} dokumentov; prečítaš ich s cesta=…) — pri každom dátum poslednej "
        "zmeny podľa gitu projektu.",
    ]
    if latest:
        lines.append(f"Posledná dokončená verzia: {latest}" + (f" ({_shown(since)})" if since else "") + ". " + MARKS)
    lines.append(RULE)
    for path in found[:DOCS_MAX]:
        when = changed.get(path)
        line = f"- {path} — {_shown(when) if when else 'dátum neznámy (nie je v gite)'}"
        version = _VERSION_DOC.search(path)
        if version:
            line += f", verzia {version.group(1)}"
            if latest and version.group(1) != latest:
                line += f" — {OLDER_VERSION}"
        elif when and since and when < since:
            line += f" — {UNCHANGED}"
        lines.append(line)
    if len(found) > DOCS_MAX:
        lines.append(f"(a ďalších {len(found) - DOCS_MAX})")
    return "\n".join(lines)
