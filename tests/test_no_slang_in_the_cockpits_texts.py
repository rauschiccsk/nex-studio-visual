"""DEV-129 — the cockpit speaks without slang: „aplikácia“, never its slang short form.

15.10.2026 the Director read a deploy warning of NEX Websites that said the short slang form of „aplikácia“; the
cockpit's own texts carried it in 114 lines — messages for the Manažér, the charters the build agents read (and so
copied into the applications they build) and the tests that pinned those messages. The rule of communication: no
slang — „aplikácia, nie appka“.

Judged by a rule over every text file the repository holds, not by a list of places. The first guard read five
folders and five suffixes; the deploy script's own message (scripts/deploy-prod.sh), a stylesheet comment, the mockup
and 41 places of the documentation slipped past it (16.10.2026). One exception, and it is a rule too: a quotation in
Slovak quotation marks („…“) stays word for word — what the Director or a specification said is not rewritten.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Every form of the slang word — each case, singular and plural (the genitive plural has its own stem), any case of
#: letters. Built from parts so this guard itself never reads as slang.
SLANG = re.compile(r"(?<![A-Za-z_])" + "a" + r"pp(?:k(?:a|y|u|e|ám|ou|ami|ách)|iek)\b", re.IGNORECASE)
#: A quotation: opened by „, closed by “, ” or " — on the same line or a few lines on. Bounded, so a stray „ cannot hide
#: the rest of a file.
QUOTATION = re.compile(r"„[^„“”\"]{0,400}[“”\"]")


def _texts():
    """Every file git holds or would add (tracked and untracked, not ignored) that is text — with its content."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    for name in filter(None, listed.split("\0")):
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary: images, fonts
        yield path, data.decode("utf-8")  # a text file in another encoding fails loudly, never slips past


def slang_in(text: str) -> list[int]:
    """Line numbers where the slang word stands outside a quotation."""
    bare = QUOTATION.sub(lambda m: "\n" * m.group(0).count("\n"), text.replace("\\n", " "))
    return [n for n, line in enumerate(bare.splitlines(), 1) if SLANG.search(line)]


def test_the_guard_reads_every_text_of_the_repository():
    found = {p.relative_to(ROOT).parts[0] for p, _ in _texts()}
    assert {"backend", "frontend", "templates", "docs", "scripts", "tests", "migrations"} <= found, (
        f"the guard read only {sorted(found)}"
    )


def test_no_text_of_the_cockpit_uses_the_slang_word():
    hits = [f"{path.relative_to(ROOT)}:{n}" for path, text in _texts() for n in slang_in(text)]
    assert hits == [], f"slang instead of „aplikácia“: {hits[:10]}"


def test_a_quotation_stays_word_for_word_but_nothing_else_hides_behind_it():
    slang = "a" + "ppka"
    assert slang_in(f'Director: „do tej istej {slang} — nechcem".') == []
    assert slang_in(f'> *„Zle nastavená {slang} vie\n> podávať starú verziu."*') == []
    assert slang_in(f"{slang.capitalize()} je nasadená.") == [1]
    assert slang_in(f"„citát“ a potom {slang}") == [1]
    assert slang_in(f"riadok\n„nedokončený citát a {slang}") == [2]
    assert slang_in(f'"{slang.capitalize()} sa nespustila: {{reason}}"') == [1]
    # a stray „ does not hide what follows far later, and the lines after a quotation keep their numbers
    assert slang_in("„" + "x" * 500 + f' {slang}"') == [1]
    assert slang_in(f'„citát\npokračuje"\na potom {slang}') == [3]


def test_every_form_of_the_word_is_caught():
    stem = "a" + "pp"
    for form in ("ka", "ky", "ku", "ke", "kám", "kou", "kami", "kách", "iek"):
        assert slang_in(f"text {stem}{form} text") == [1], stem + form
        assert slang_in(f"{(stem + form).upper()}") == [1], stem + form


# --- DEV-178: what the blanket replacement left behind ------------------------------------------------------------
#
# The replacement of 15.10.2026 rewrote the slang word inside quotations too, and Poradca's charter told it not to use
# slang with „aplikácia“ as both the word to use and the word to avoid — a rule with no example of what not to say. A
# sentence that sets a word against itself is what such a replacement leaves; none is ever meant.

#: A word set against itself („aplikácia“ against „aplikácia“), the second one possibly in a quotation.
SELF_CONTRAST = re.compile(r"(?<!\w)(\w+),? nie [„\"]?\1(?!\w)")


def test_no_text_sets_a_word_against_itself():
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path, text in _texts()
        for n, line in enumerate(text.splitlines(), 1)
        if SELF_CONTRAST.search(line)
    ]
    assert hits == [], f"a word set against itself — what a blanket replacement leaves: {hits[:10]}"


def test_a_word_set_against_itself_is_caught_and_a_real_contrast_is_not():
    """Built from parts, so this guard's own file never reads as the damage it looks for."""
    slang, word, nie = "a" + "ppka", "aplikácia", " nie "
    assert SELF_CONTRAST.search(f'slang nepoužívaj ({word},{nie}„{word}").')
    assert SELF_CONTRAST.search(f"{word}{nie}{word}")
    assert not SELF_CONTRAST.search(f"slang nepoužívaj ({word},{nie}„{slang}“).")
    assert not SELF_CONTRAST.search(f"{word},{nie}aplikácie")
