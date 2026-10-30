"""DEV-263 — Fakturácia counts delivered tokens in kilotokens: 1 kT = 1 000 tokens, rounded UP to one tenth.

The Director 30.10.2026: „kilo tokeny zaokrúhliť hore na 1 desatinné miesto príklad 1451.4 kT x 2 Eur = 2902.80 Eur“.
One rule for the statement's money (``billing_category.value``), its CSV and every screen that shows tokens.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

TOKENS_PER_KT = Decimal(1000)
#: One tenth — the step of a kilotoken count and of a statement line's euros.
TENTH = Decimal("0.1")


def kilotokens(tokens: int) -> Decimal:
    """*tokens* in kT, rounded up to a tenth (1 451 301 → 1 451,4; 1 451 401 → 1 451,5)."""
    return (Decimal(tokens) / TOKENS_PER_KT).quantize(TENTH, rounding=ROUND_CEILING)
