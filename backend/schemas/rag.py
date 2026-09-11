"""Odpovede vyhľadávacej vrstvy, ktoré majú vlastný tvar (ICCINT-111)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KbIndexStatusRead(BaseModel):
    """Sedí RAG index so Znalostnou bázou?

    Čísla sa POČÍTAJU naživo z disku a z indexu — nič sa neukladá, takže sa údaj nemôže rozísť s tým,
    čo v indexe naozaj je. Keď je niektorá strana nedostupná, endpoint vráti 503 a NIE tento model:
    nula rozdielov z nedostupného zdroja vyzerá presne ako poriadok a bola by horšia než chyba samotná.
    """

    on_disk: int = Field(description="Koľko dokumentov Znalostnej bázy leží na disku (bez tajomstiev).")
    indexed: int = Field(description="Koľko dokumentov pozná index.")
    out_of_sync: int = Field(description="Súčet nezhôd — chýbajúce + zastarané + osirelé. Nula = sedí.")
    missing: int = Field(description="Na disku sú, v indexe nie.")
    stale: int = Field(description="V indexe sú, ale staršie než ich podoba na disku.")
    orphaned: int = Field(description="V indexe zostali, hoci na disku už nie sú.")
    last_indexed_at: Optional[datetime] = Field(
        default=None, description="Najnovší zápis do indexu naprieč korpusom (None = index je prázdny)."
    )
    sample: list[str] = Field(
        default_factory=list,
        description="Prvých pár nesediacich dokumentov — aby číslo nebolo len číslo. Cesty v rámci "
        "Znalostnej bázy, nikdy nie tajomstvá (tie sa do porovnania vôbec nedostanú).",
    )
