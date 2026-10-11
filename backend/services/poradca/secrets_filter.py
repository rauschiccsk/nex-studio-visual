"""Poradca secrets filter — layers 2 and 3 of the design (``docs/specs/poradca.md`` §4.4).

Layer 1 is what Poradca does not receive at all (restricted mode, overlaid files, columns without permission). Here are
two more, and they run on the OUTPUT of every tool and on the answer before it is saved:

  2. **known values** — exact match with the secrets the backend knows (Claude login, login-signing key,
     GitHub token, cockpit database password, secret variables of the UAT installation, project credentials
     from the vault). A value shorter than :data:`MIN_KNOWN_LENGTH` is not searched for — a short string would hide
     ordinary words and the answer would become unreadable without protecting anything;
  3. **shapes** — ``token=…``, ``Bearer …``, password in a database URL, JWT, PEM private key, GitHub and Anthropic
     tokens. It also catches a secret the backend does not know.

The filter is idempotent and never raises — a filter that fails would either drop the answer or let it through
unfiltered.
"""

from __future__ import annotations

import re
from typing import Iterable

#: What the human sees instead of the secret.
REDACTED = "‹skryté›"

#: The shortest known value that is searched for by exact match.
MIN_KNOWN_LENGTH = 8

_SECRET_KEY_WORDS = (
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|auth[_-]?token|token|secret[_-]?key|secret"
    r"|client[_-]?secret|password|passwd|pwd|private[_-]?key"
)

#: Secret shapes (layer 3). Order: whole blocks first, then shapes with a key, finally standalone tokens.
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
        REDACTED,
    ),
    # password in a database or queue URL: scheme://user:PASSWORD@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@"), rf"\1:{REDACTED}@"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {REDACTED}"),
    (re.compile(r"(?im)^(\s*authorization\s*[:=]\s*).+$"), rf"\1{REDACTED}"),
    # KEY=value, KEY: value, "key": "value", ?token=value — also in prefixed names (DB_PASSWORD)
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
    """Replaces known values and secret shapes with :data:`REDACTED`."""

    def __init__(self, known_values: Iterable[str] = ()) -> None:
        values = {v.strip() for v in known_values if isinstance(v, str)}
        # Longer first: if one value were the beginning of another, the shorter would leave a tail of the longer.
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
        except Exception:  # noqa: BLE001 — the filter must never let text through unfiltered
            return REDACTED
        return text
