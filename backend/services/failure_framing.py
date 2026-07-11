"""Plain-language framing for pipeline FAILURE reasons (self-sufficiency kernel, 2026-07-10).

The cockpit's failure *scaffolding* (status, next_action, Decision-Card intro) is already plain Slovak, but the
actual WHY often arrived as RAW technical text — a release-smoke boot probe error, or the tail of the acceptance
script's stdout (``release_smoke_test.sh exit 1: <400 chars>``). A non-expert Manažér (Tibor/Nazar) can't read
that; today only Dedo (at a terminal, reading logs) could explain it. That is exactly the crutch the kernel
removes: the manager must SEE what failed + WHY, in plain language, IN the cockpit.

This module is the first slice of a plain-language framing layer: a DETERMINISTIC translator of the KNOWN raw
failure shapes into a plain-Slovak WHY, with a safe generic fallback for anything unrecognised. The raw string
is never discarded — the call sites keep it in ``payload.technical_detail`` so the FE can offer it under a
collapsible "Technický detail". Deterministic (not an AI turn) → reliable, instant, zero tokens; the richer
"AI explains the failure conversationally" model (conversation-as-foundation) folds in later on top of this.
"""

from __future__ import annotations

import re

#: Boot timeout shape: ``app did not boot / not responding within 900s: <probe error>`` (``_run_app_starts_smoke``).
_BOOT_TIMEOUT_RE = re.compile(r"within\s+(\d+)\s*s", re.IGNORECASE)


def humanize_release_failure(raw: str) -> str:
    """A plain-Slovak WHY clause for a raw release-smoke / boot / acceptance failure *detail*.

    Returns a lowercase-initial clause (no trailing period) meant to read after a dash, e.g.
    ``"Skúška spustenia — " + humanize_release_failure(detail)``. Pattern-matches the known raw shapes the
    engine produces; an unrecognised detail still yields a plain (never raw) fallback. The raw string stays
    available separately as the technical detail — this is only the human headline.
    """
    r = (raw or "").strip()
    low = r.lower()

    # INFRA / DEPLOYMENT-CONFIG cause (checked FIRST): docker/compose could not even bring the stack UP — a
    # missing env value, a port clash — so the app never started and NO check ever ran. Distinguished from a
    # genuine app boot/acceptance failure so the manager isn't told "some checks failed" (→ go fix the app)
    # when the real cause is the scaffold / deployment settings (→ complete the settings). The raw ``exit N``
    # shape would otherwise fall into the generic acceptance branch below and misattribute it to the app's code.
    if "interpolat" in low or "required variable" in low or "is not set" in low or "variable is not set" in low:
        return (
            "v nastavení nasadenia chýba potrebná hodnota (napríklad heslo k databáze) — "
            "nie je to chyba v kóde aplikácie"
        )
    if "port is already allocated" in low or "address already in use" in low or "bind for" in low:
        return "potrebný sieťový port už používa iný program — nie je to chyba v kóde aplikácie"

    # Boot never came up — the app (or its DB) failed to start within the boot window.
    if "did not boot" in low or "not responding within" in low:
        m = _BOOT_TIMEOUT_RE.search(low)
        if m:
            mins = max(1, round(int(m.group(1)) / 60))
            return f"aplikácia sa nespustila do {mins} min — štart aplikácie alebo databázy zlyhal"
        return "aplikácia sa nespustila — štart aplikácie alebo databázy zlyhal"

    # The web frontend container never began serving.
    if "not serving" in low:
        return "webové rozhranie sa nespustilo"

    # The acceptance script ran but some check did not pass (``release_smoke_test.sh exit N: …``).
    if "release_smoke_test" in low or re.search(r"\bexit\s+[1-9]", low):
        return "automatická skúška po spustení neprešla (niektoré kontroly zlyhali)"

    # Unrecognised — keep it plain (the raw detail is preserved as technical_detail by the caller).
    return "skúška po spustení zlyhala"
