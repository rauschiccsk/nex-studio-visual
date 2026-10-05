"""Filter tajomstiev Poradcu — vrstvy 2 a 3 z návrhu (``docs/specs/poradca.md`` §4.4).

Vrstva 1 je to, čo Poradca nedostane vôbec (obmedzený režim, prekryté súbory, stĺpce bez práva). Tu sú
ďalšie dve a bežia na VÝSTUPE každého nástroja aj na odpovedi pred uložením:

  2. **známe hodnoty** — presná zhoda s tajomstvami, ktoré backend pozná (prihlásenie Claude, kľúč podpisu
     prihlásení, token GitHubu, heslo databázy kokpitu, tajné premenné inštalácie UAT, prístupy projektu
     z trezoru). Hodnota kratšia než :data:`MIN_KNOWN_LENGTH` sa nehľadá — krátky reťazec by skryl bežné
     slová a odpoveď by sa stala nečitateľnou bez toho, aby niečo chránila;
  3. **tvary** — ``token=…``, ``Bearer …``, heslo v adrese databázy, JWT, súkromný kľúč PEM, tokeny GitHubu
     a Anthropicu. Chytí aj tajomstvo, ktoré backend nepozná.

Filter je idempotentný a nikdy nevyhodí výnimku — filter, ktorý padne, by odpoveď buď zahodil, alebo
pustil nefiltrovanú.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Čo človek uvidí namiesto tajomstva.
REDACTED = "‹skryté›"

#: Najkratšia známa hodnota, ktorá sa hľadá presnou zhodou.
MIN_KNOWN_LENGTH = 8

_SECRET_KEY_WORDS = (
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|auth[_-]?token|token|secret[_-]?key|secret"
    r"|client[_-]?secret|password|passwd|pwd|private[_-]?key"
)

#: Tvary tajomstiev (vrstva 3). Poradie: najprv celé bloky, potom tvary s kľúčom, nakoniec samostatné tokeny.
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
        REDACTED,
    ),
    # heslo v adrese databázy či fronty: scheme://user:HESLO@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@"), rf"\1:{REDACTED}@"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {REDACTED}"),
    (re.compile(r"(?im)^(\s*authorization\s*[:=]\s*).+$"), rf"\1{REDACTED}"),
    # KĽÚČ=hodnota, KĽÚČ: hodnota, "kľúč": "hodnota", ?token=hodnota — aj v názvoch s predponou (DB_PASSWORD)
    (
        re.compile(rf"(?i)((?:[A-Za-z0-9]+[_-])*(?:{_SECRET_KEY_WORDS})[\"']?\s*[:=]\s*[\"']?)([^\s\"'&,;<>]{{3,}})"),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{16,}"), REDACTED),
)


class SecretFilter:
    """Nahradí známe hodnoty a tvary tajomstiev za :data:`REDACTED`."""

    def __init__(self, known_values: Iterable[str] = ()) -> None:
        values = {v.strip() for v in known_values if isinstance(v, str)}
        # Dlhšie najprv: keby bola jedna hodnota začiatkom druhej, kratšia by z dlhšej nechala chvost.
        self._known = sorted((v for v in values if len(v) >= MIN_KNOWN_LENGTH), key=len, reverse=True)

    @property
    def known_count(self) -> int:
        return len(self._known)

    def __call__(self, text: str) -> str:
        if not text:
            return text
        try:
            for value in self._known:
                if value in text:
                    text = text.replace(value, REDACTED)
            for pattern, replacement in _SHAPES:
                text = pattern.sub(replacement, text)
        except Exception:  # noqa: BLE001 — filter nesmie nikdy pustiť text bez filtrovania
            return REDACTED
        return text
