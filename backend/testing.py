"""Test utilities for NEX Studio backend.

Provides helpers for creating FastAPI TestClient with DB session override,
and common test factory functions.
"""


# ---------------------------------------------------------------------------
# Príznak živého náhľadu — jedno čítanie pre VŠETKY stráže (ICCINT-108)
# ---------------------------------------------------------------------------


def preview_accepted_values(text: str, *, src_dir=None):
    """Hodnoty, ktoré appka prijme ako „zapni náhľad“; ``None`` = kontroluje PRAVDIVOSTNE.

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
