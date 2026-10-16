"""DEV-129 — the cockpit speaks without slang: „aplikácia“, never its slang short form.

15.10.2026 the Director read a deploy warning of NEX Websites that said the short slang form of „aplikácia“; the
cockpit's own texts carried it in 114 lines — messages for the Manažér, the charters the build agents read (and so
copied into the applications they build) and the tests that pinned those messages. The rule of communication: no
slang — „aplikácia, nie appka“.

Judged by a rule over every source the cockpit ships or hands to an agent, not by a list of known messages — and over
the release notes its *Aktualizácie* tab shows, read from where that tab reads them.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import release_notes

ROOT = Path(__file__).resolve().parents[1]
#: Every form of the slang word — each case, singular and plural (the genitive plural has its own stem), any case of
#: letters. Built from parts so this guard itself never reads as slang.
SLANG = re.compile(r"(?<![A-Za-z_])" + "a" + r"pp(?:k(?:a|y|u|e|ám|ou|ami|ách)|iek)\b", re.IGNORECASE)
PLACES = ("backend", "frontend/src", "templates")
SUFFIXES = {".py", ".ts", ".tsx", ".md", ".json"}


def _sources():
    for place in PLACES:
        for path in (ROOT / place).rglob("*"):
            if path.suffix in SUFFIXES and "node_modules" not in path.parts and path.is_file():
                yield path
    yield from release_notes.DOCS_VERSIONS_ROOT.glob(release_notes.RELEASE_NOTES_GLOB)


def test_the_guard_reads_the_sources_it_guards():
    found = {p.relative_to(ROOT).parts[0] for p in _sources()}
    assert {"backend", "frontend", "templates", "docs"} <= found, f"the guard read only {sorted(found)}"


def test_no_text_of_the_cockpit_uses_the_slang_word():
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in _sources()
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if SLANG.search(line.replace("\\n", " "))
    ]
    assert hits == [], f"slang instead of „aplikácia“: {hits[:10]}"
