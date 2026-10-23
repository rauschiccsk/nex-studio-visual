"""Test utilities for NEX Studio backend.

Provides helpers for creating FastAPI TestClient with DB session override,
and common test factory functions.
"""


# ---------------------------------------------------------------------------
# Príznak živého náhľadu — jedno čítanie pre VŠETKY stráže (ICCINT-108)
# ---------------------------------------------------------------------------


def preview_accepted_values(text: str, *, src_dir=None):
    """Hodnoty, ktoré aplikácia prijme ako „zapni náhľad“; ``None`` = kontroluje PRAVDIVOSTNE.

    ⚠️ **Býva to napísané na dvoch miestach a to je práve ten problém.** Stráže nad príznakom náhľadu
    stoja v dvoch stromoch testov (``backend/tests`` aj ``tests/services``) a každá si to čítanie
    písala sama. 10.09.2026 sa rozišli: jedna sa naučila sledovať pomocníka, druhá nie — a spadla na
    šablóne, ktorá bola v poriadku. Odvtedy je to čítanie tu, na jednom mieste.

    Hľadá sa v dvoch krokoch:

    1. porovnania priamo v texte (``VITE_PREVIEW === "…"``);
    2. keď tam nie sú, sleduje sa import ``isPreviewEnabled`` a číta sa zoznam v tom súbore —
       rozumný vývojár rozhodovanie z podmienky vytiahne a stráž ho musí vidieť aj tam.
    """
    import re
    from pathlib import Path

    literals = re.findall(r'(?:VITE_PREVIEW|previewFlag)\s*===\s*"([^"]*)"', text)
    if literals:
        return tuple(literals)

    if src_dir is not None and re.search(r"\bisPreviewEnabled\b", text):
        m = re.search(r'import\s*\{[^}]*\bisPreviewEnabled\b[^}]*\}\s*from\s*"([^"]+)"', text)
        if m:
            rel = m.group(1).lstrip("./")
            for pripona in (".ts", ".tsx", "/index.ts"):
                pomocnik = Path(src_dir) / (rel + pripona)
                if pomocnik.is_file():
                    zoznam = re.search(r"=\s*\[([^\]]*)\]", pomocnik.read_text(encoding="utf-8"))
                    if zoznam:
                        return tuple(re.findall(r'"([^"]*)"', zoznam.group(1)))
    return None


# ---------------------------------------------------------------------------
# Pin nex-shared — prečítanie späť toho, čo zapísal rewrite_pin (DEV-155: ostrý kód ho nečíta)
# ---------------------------------------------------------------------------


def parse_pin(package_json_text: str):
    """The nex-shared version a package.json pins (``'0.11.0'``), or ``None`` — the tests read back what
    :func:`backend.services.nexshared.rewrite_pin` wrote, with the same pattern it writes by."""
    import json

    from backend.services.nexshared import _PIN_RE

    try:
        data = json.loads(package_json_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (data.get("dependencies") or {}).get("nex-shared") or (data.get("devDependencies") or {}).get("nex-shared")
    if not isinstance(dep, str):
        return None
    m = _PIN_RE.search(dep)
    return m.group(1) if m else None
