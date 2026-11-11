"""The text a person passes on from an answer is Slovak (DEV-356).

Director 09.10.2026: „celá komunikácia s Agentom ide po slovensky“; 08.10.2026: „Zadanie a dokumentácia má byť po
slovensky.“ 11.11.2026 the Poradca of NEX Knowledge answered in Slovak but wrote its instruction for the Staviteľ in
English — 1 of its 183 instructions so far; the finding it answered was about translating code into English. The
charters said the answer is Slovak and never said it of the blocks, and nothing checked them.

The blocks a person passes on as text — an instruction for the build's agent, an answer for it marked as a choice, a
requirement for the Zásobník, an idea's brief for a new project and its addition to an existing one — are read here
sentence by sentence. A sentence is English when it carries at least two common English words and more of them
than Slovak ones; paths, names from code, quoted screen texts and code blocks are not speech and are left out. The
method is the gate the Dedo's own texts pass (``kokpit_navrh.py``, measured on 137 texts); here it was measured on
every block the Poradca and the Projektant wrote up to 11.11.2026 — 311 blocks in 230 answers (183 instructions,
111 choices, 10 requirements, 6 briefs, 1 addition): the one English instruction is found, nothing else.

An answer with such a block goes back to its agent once to be rewritten (:mod:`runner`); one that still carries it
offers nothing of it and says why (the read of the answer, the click that would use it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.services.poradca.handoff import (
    BLOCK_BACKLOG,
    BLOCK_BACKLOG_LEGACY,
    BLOCK_IDEA_ADDITION,
    BLOCK_IDEA_PROJECT,
    BLOCK_INSTRUCTION,
)

#: The marked choice (DEV-83 / DEV-224) — not a ``handoff`` block, but text the person passes on too.
BLOCK_CHOICE = "volba"

#: The blocks a person passes on as text, with the name the screen and the agent know them by.
TEXT_BLOCKS: dict[str, str] = {
    BLOCK_INSTRUCTION: "pokyn pre agenta",
    BLOCK_CHOICE: "odpoveď pre agenta",
    BLOCK_BACKLOG: "požiadavka do Zásobníka",
    BLOCK_BACKLOG_LEGACY: "požiadavka do Zásobníka",
    BLOCK_IDEA_PROJECT: "zadanie projektu",
    BLOCK_IDEA_ADDITION: "doplnenie projektu",
}

#: Parts of a text that are not speech: a code block and span, an address, a quotation (a screen text, a message).
_NOT_SPEECH = re.compile(
    r"```.*?```|`[^`\n]*`|https?://\S+|„[^“\n]*“|“[^”\n]*”|\"[^\"\n]*\"",
    re.S,
)
#: A word from code, not from speech: a path, a file, a flag, a variable, CamelCase, an ABBREVIATION.
_TECHNICAL = re.compile(r"[/\\_=<>{}()\[\]#@$]|\.\w|^--?\w|^[A-Z0-9]{2,}$|[a-z][A-Z]")
_DIACRITICS = re.compile(r"[áäčďéíĺľňóôŕšťúýž]", re.I)
#: Common English words that do not stand in a Slovak sentence. Words that are Slovak too (a, to, do, on, no, so, by,
#: pre) are left out — „Najprv to podstatné, lebo si za to nemohol“ is a Slovak sentence.
_ENGLISH = frozenset(
    "the and is are was were be been of in for with that this these those it its as not must should will would "
    "when only from or an each every which if nothing else add use run fix amend keep before after than then there "
    "here has have had into out without also all any your you we they them their what where who how but".split()
)
#: Common Slovak words without diacritics (a word with diacritics is known by them).
_SLOVAK = frozenset(
    "je sa na v vo nie ako pre s z zo k ku ten ta tie aj ale alebo len ked ktory bez po pri od uz tri veci zvysok "
    "su bol bola bude ma maju tak tu tam co kde ich jeho jej iba podla tento tato toto ani este".split()
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
#: A shorter sentence (a list of names, a heading, a line ``Názov: …``) does not decide the language.
MIN_WORDS = 4


@dataclass(frozen=True)
class NotSlovak:
    """A block of the answer that is not Slovak, with its English sentences."""

    block: str
    sentences: tuple[str, ...]

    @property
    def label(self) -> str:
        return TEXT_BLOCKS[self.block]


def _words(sentence: str) -> list[str]:
    tokens = [t for t in re.findall(r"\S+", sentence) if not _TECHNICAL.search(t)]
    letters = (re.sub(r"[^\w]", "", t, flags=re.U).lower() for t in tokens)
    return [w for w in letters if w and not w.isdigit()]


def english_sentences(text: str) -> list[str]:
    """The sentences of ``text`` that are English — empty when the text is Slovak."""
    found = []
    for sentence in _SENTENCE.split(_NOT_SPEECH.sub(" ", text or "")):
        words = _words(sentence)
        if len(words) < MIN_WORDS:
            continue
        english = sum(1 for w in words if w in _ENGLISH)
        slovak = sum(1 for w in words if w in _SLOVAK or _DIACRITICS.search(w))
        if english >= 2 and english > slovak:
            found.append(" ".join(sentence.split()))
    return found


def _bodies(answer: str, block: str) -> list[str]:
    """Every ``<block …>…</block>`` of the answer — with its attributes too: a choice names its card
    (``karta="1"``), an addition its project (``projekt="nex-manager"``)."""
    name = re.escape(block)
    return re.findall(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", answer or "", re.S)


def not_in_slovak(answer: str) -> list[NotSlovak]:
    """The text blocks of an answer that are not Slovak, in the order of :data:`TEXT_BLOCKS`."""
    result = []
    for block in TEXT_BLOCKS:
        sentences = [s for body in _bodies(answer, block) for s in english_sentences(body)]
        if sentences:
            result.append(NotSlovak(block, tuple(sentences)))
    return result


def _labels(found: list[NotSlovak]) -> str:
    labels: list[str] = []
    for one in found:
        if one.label not in labels:
            labels.append(one.label)
    return ", ".join(labels)


def refusal(found: list[NotSlovak]) -> str:
    """What the screen says under an answer whose block is not Slovak — and what the click that would use it answers."""
    return (
        f"Kokpit neponúka, čo nie je po slovensky: {_labels(found)}. "
        "Požiadaj o prepísanie po slovensky — cesty a mená z kódu ostanú, ako sú."
    )


#: How many English sentences of each block the agent is shown when its answer goes back.
SHOWN_SENTENCES = 3


def rewrite_directive(found: list[NotSlovak]) -> str:
    """The turn that sends an answer back to its agent: which block is not Slovak, with its English sentences."""
    lines = []
    for one in found:
        shown = "; ".join(f"„{s[:200]}“" for s in one.sentences[:SHOWN_SENTENCES])
        lines.append(f"- {one.label} (`<{one.block}>`): {shown}")
    listed = "\n".join(lines)
    return (
        "[Kontrola kokpitu] Tvoja odpoveď nesie text, ktorý človek odovzdá ďalej, a ten nie je po slovensky — "
        f"kokpit ho neponúkne:\n{listed}\n\n"
        "Pokyn pre agenta, odpoveď pre agenta, požiadavka do Zásobníka, zadanie aj doplnenie projektu sú vždy po "
        "slovensky — Director: „celá komunikácia s Agentom ide po slovensky“. Cesty, mená súborov, funkcií a kódov "
        "ostávajú presne ako v kóde; daj ich do `…`.\n"
        "Napíš celú odpoveď znova, vecne rovnakú, s týmito blokmi po slovensky. Nič nové nezisťuj a nič iné nemeň."
    )


def step_target(found: list[NotSlovak]) -> str:
    """The step the person sees while the answer is being rewritten."""
    return _labels(found)
