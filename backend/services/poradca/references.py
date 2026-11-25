"""DEV-511 — the list of what a new project needs from the internet, in its brief (``<podklady>``).

25.11.2026 the Projektant of NEX DualBook found ten documents the project needs (XSD schemas of VAT, forms, the bank's
integration manual) and wrote into the brief that the Manažér downloads them into ``docs/reference/`` — it reads the
internet but cannot save a file, the Staviteľ has no internet, and the cockpit had no way to bring a file in. Director:
„…mám ich vložiť ručne čo ja robiť nebudem.“ Now the Projektant lists them, one per line
``cesta | adresa | opis``, and the cockpit downloads them itself when the project is founded
(:mod:`backend.services.reference_docs`). The list stays in the brief, so the Staviteľ knows where they are.

A list with a wrong line goes back to the Projektant once (:mod:`runner`), and a brief that still carries one is not
offered (:func:`handoff.withheld`): an item without an exact address would otherwise end as manual work again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

#: The block inside the brief of a new project.
BLOCK = "podklady"
#: Where the documents go in the project — the Staviteľ reads them there, without the internet.
FOLDER = "docs/reference/"
#: Documents only — what the download checks by content (:mod:`reference_docs`).
EXTENSIONS = (
    "pdf", "doc", "docx", "xls", "xlsx", "odt", "ods", "zip", "xsd", "xml", "txt", "md", "csv", "json", "html",
)  # fmt: skip
#: More is not a list of documents for one project any more.
MAX_ITEMS = 60

_PATH = re.compile(r"docs/reference/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")
_PATH_MAX = 200

NO_ADDRESS = (
    "{path}: chýba presná adresa https:// súboru — nájdi ju na internete, alebo položku vynechaj a povedz prečo."
)
BAD_PATH = "{path}: cesta musí byť v priečinku docs/reference/ (písmená bez diakritiky, číslice, . _ -)."
BAD_KIND = "{path}: kokpit sťahuje len dokumenty ({kinds})."
BAD_PORT = "{path}: adresa musí byť na štandardnom porte https (bez :port)."
NO_DESCRIPTION = "{path}: chýba opis, čo súbor je."
BAD_LINE = "riadok „{line}“ nemá tvar „cesta | adresa | opis“."
TWICE = "{path}: tá istá cesta je v zozname dvakrát."
TOO_MANY = "zoznam má viac ako {max} položiek."

#: What the person sees while the answer goes back to the Projektant (the step of :mod:`runner`).
STEP_TARGET = "zoznam podkladov z internetu"

REFUSAL = (
    "Kokpit neponúka odpoveď, ktorej zoznam podkladov z internetu nie je úplný: {problems} "
    "Požiadaj o opravu zoznamu podkladov."
)


@dataclass(frozen=True)
class Reference:
    """One document: where it goes in the project, where it is on the internet, and what it is."""

    path: str
    url: str
    description: str


def _block(text: str) -> Optional[str]:
    """The lines between ``<podklady>`` and ``</podklady>``, each on a line of its own — the tag named in a sentence
    (as the charter does) is no block."""
    match = re.search(rf"(?ms)^[ \t]*<{BLOCK}>[ \t]*\n(.*?)^[ \t]*</{BLOCK}>[ \t]*$", text or "")
    return match.group(1) if match else None


def _path_problem(path: str) -> Optional[str]:
    if len(path) > _PATH_MAX or not _PATH.fullmatch(path) or any(part in (".", "..") for part in path.split("/")):
        return BAD_PATH.format(path=path)
    extension = path.rsplit(".", 1)[-1].casefold() if "." in path.rsplit("/", 1)[-1] else ""
    if extension not in EXTENSIONS:
        return BAD_KIND.format(path=path, kinds=", ".join(EXTENSIONS))
    return None


def _address_problem(path: str, url: str) -> Optional[str]:
    if " " in url or not url.startswith("https://"):
        return NO_ADDRESS.format(path=path)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return NO_ADDRESS.format(path=path)
    if not parts.hostname or parts.username or parts.password:
        return NO_ADDRESS.format(path=path)
    if port not in (None, 443):
        return BAD_PORT.format(path=path)
    return None


def of_brief(brief: str) -> tuple[list[Reference], list[str]]:
    """The documents the brief lists, and what is wrong with the list (sentences, in the order of the lines).
    ``([], [])`` for a brief without the list."""
    body = _block(brief)
    if body is None:
        return [], []
    found: list[Reference] = []
    problems: list[str] = []
    seen: set[str] = set()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) > MAX_ITEMS:
        return [], [TOO_MANY.format(max=MAX_ITEMS)]
    for line in lines:
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            problems.append(BAD_LINE.format(line=line[:120]))
            continue
        path, url, description = parts
        problem = _path_problem(path)
        if problem is None:
            problem = TWICE.format(path=path) if path in seen else None
            seen.add(path)
        problem = problem or _address_problem(path, url)
        if problem is None and not description:
            problem = NO_DESCRIPTION.format(path=path)
        if problem:
            problems.append(problem)
            continue
        found.append(Reference(path, url, description))
    return found, problems


def _outside_the_brief(answer: str) -> str:
    """The answer without the brief of a new project — a list there is the founding's (:func:`of_brief`)."""
    from backend.services.poradca import handoff  # it reads the blocks of this module

    block = handoff.BLOCK_IDEA_PROJECT
    return re.sub(rf"<{block}>.*?</{block}>", "", answer or "", flags=re.S)


def of_answer(answer: str) -> tuple[list[Reference], list[str]]:
    """DEV-513 — the documents a project's Poradca offers to add to the project („Stiahnuť do projektu“): the list in
    the answer itself, not in a brief of a new project."""
    return of_brief(_outside_the_brief(answer))


def problems_in_answer(answer: str) -> list[str]:
    """What is wrong with a list of documents in the answer — in its brief of a new project or offered on its own —
    ``[]`` when there is none."""
    from backend.services.poradca import handoff  # it reads the blocks of this module

    body = handoff.extract_block(answer, handoff.BLOCK_IDEA_PROJECT)
    return (of_brief(body)[1] if body else []) + of_answer(answer)[1]


def refusal(problems: list[str]) -> str:
    """What the screen says under an answer whose list is wrong — and what the click that would use it answers."""
    return REFUSAL.format(problems=" ".join(problems))


def rewrite_directive(problems: list[str]) -> str:
    """The turn that sends the answer back to the Projektant: what is wrong with the list."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        "[Kontrola kokpitu] Zoznam podkladov z internetu (`<podklady>`) nie je úplný — kokpit ho neponúkne:\n"
        f"{listed}\n\n"
        "Každý riadok má tvar `cesta | adresa | opis`: cesta v priečinku docs/reference/, presná adresa https:// "
        "súboru (nie stránky, ktorá naň odkazuje) a opis. Kokpit ich stiahne sám — aj keď ti ich web prečítať "
        "nedovolí — a Manažérovi sťahovanie neukladaj. Napíš celú odpoveď znova s opraveným zoznamom."
    )
