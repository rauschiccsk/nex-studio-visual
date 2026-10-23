"""DEV-205 — Poradca sends the Manažér to „Vložiť do odpovede“, not to copying the answer into Riadiace centrum.

23.10.2026 Poradca ended its answer to NEX Manager 1.4.0's question with „V Riadiacom centre klikni na „Odpovedať (agent
sa pýta)“ a pošli:“ — the manual way, beside an answer the cockpit can insert in one click. Director: „chcel by som aby
aj tu bola možnosť vložiť odpoveď poradcu do chatu Agenta“.
"""

from __future__ import annotations

from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"


def test_the_charter_sends_the_manager_to_the_button():
    charter = CHARTER.read_text(encoding="utf-8")

    assert "Manažéra neposielaj odpoveď prepisovať do Riadiaceho centra" in charter
    assert "klikne „Vložiť do odpovede“" in charter
