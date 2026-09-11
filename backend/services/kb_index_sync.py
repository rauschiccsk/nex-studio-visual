"""Znalostná báza ↔ RAG index: porovnanie a dorovnanie (ICCINT-111).

**Prečo to vzniklo.** Zmerané 10.09.2026: z 205 súborov Znalostnej bázy bolo 71 v indexe vôbec a
ďalších 42 starších než disk — **113 z 205 (55 %) nesedelo**. `icc/STRUCTURE.md`, povinné čítanie pri
štarte každej session, v indexe nebolo; celá špecifikácia NEX Inboxu bola 63 dní pozadu. Kto sa RAG-u
opýtal, dostal odpoveď zo sveta, ktorý už neplatil — a nijako sa nedozvedel, že ju dostal.

Príčina nebola nedbalosť. Indexer existoval (`backend/rag/indexer.py`), ale **nespúšťalo ho nič** —
žiadny časovač, cron ani sledovač. A návod bol kruhový: hlavný ``CLAUDE.md`` §13 posielal na
``.claude/agents/<rola>/CLAUDE.md``, a rola odpovedala „RAG reindex (per §13 hlavného)". Konkrétny
príkaz nebol uvedený nikde, takže sa roky nedal ani nájsť, nieto splniť.

**Dve zásady, na ktorých táto služba stojí:**

1. **Stav sa nikam neukladá — odvodzuje sa naživo** z disku a z indexu. Uložený údaj „naposledy
   zosynchronizované" by sa mohol rozísť s tým, čo v indexe naozaj je; odvodený sa rozísť nemôže.
   (Tá istá lekcia ako v ICCINT-117: stav, ktorý neprežije refresh — alebo ktorý tvrdí niečo iné než
   skutočnosť — nie je stav.)
2. **„Neviem" sa nikdy nevydáva za „v poriadku".** Keď je Znalostná báza nedostupná alebo Qdrant
   mlčí, vyletí :class:`KbIndexUnavailable`. Nulový rozdiel z nedostupného zdroja by bol najhorší
   možný výsledok: zelený údaj nad korpusom, o ktorom nevieme nič.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from backend.config.settings import settings
from backend.rag.indexer import NEVER_INDEX_CATEGORIES, RAGIndexer

logger = logging.getLogger(__name__)

#: Kolekcia Qdrantu sa volá rovnako ako tenant (viď ``indexer.index_document``).
TENANT = "icc"

#: Ako často slučka na pozadí dorovnáva rozdiel. Pätnásť minút je kompromis: dosť často na to, aby
#: agent po zápise do KB našiel svoj text, a dosť zriedka na to, aby embeddingy na CPU nezaťažovali
#: stroj natrvalo.
KB_SYNC_INTERVAL_SECONDS = 900

#: Súbor sa považuje za zastaraný, až keď je NOVŠÍ než jeho zaindexovaná podoba o viac než toto.
#: Bez tolerancie by sa každý súbor tvári zastaraný hneď po zápise (index vzniká o sekundy neskôr).
STALE_TOLERANCE_SECONDS = 60

#: Koľko dokumentov najviac spracuje jeden prechod. Embeddingy bežia na CPU; bez stropu by prvý
#: prechod nad rozídeným korpusom držal stroj hodiny. Zvyšok dobehne v ďalšom prechode — a KOĽKO
#: zostáva, je vidieť, takže sa strop nemôže tváriť ako hotovo.
MAX_DOCUMENTS_PER_PASS = 150


class KbIndexUnavailable(RuntimeError):
    """Porovnanie sa nedá spraviť — Znalostná báza alebo index nie sú dostupné.

    Zámerne výnimka, nie „nula rozdielov": nula z nedostupného zdroja vyzerá presne ako poriadok.
    """


@dataclass(frozen=True)
class KbIndexComparison:
    """Čo sedí a čo nie. Označenia sú cesty V RÁMCI Znalostnej bázy (``projects/x/Y.md``)."""

    on_disk: int
    indexed: int
    #: Na disku sú, v indexe nie.
    missing: tuple[str, ...]
    #: V indexe sú, ale staršie než ich podoba na disku.
    stale: tuple[str, ...]
    #: V indexe zostali, hoci na disku už nie sú — odpoveď na dokument, ktorý neexistuje.
    orphaned: tuple[str, ...]
    #: Najnovší zápis do indexu naprieč celým korpusom (None = index je prázdny).
    last_indexed_at: Optional[datetime]

    @property
    def out_of_sync(self) -> int:
        return len(self.missing) + len(self.stale) + len(self.orphaned)

    @property
    def in_sync(self) -> bool:
        return self.out_of_sync == 0


def compare(disk: Mapping[str, float], indexed: Mapping[str, datetime]) -> KbIndexComparison:
    """ČISTÉ rozhodovanie — bez disku a bez Qdrantu, aby sa dalo prejsť tabuľkou.

    ``disk`` = označenie → čas poslednej zmeny (epoch), ``indexed`` = označenie → čas zaindexovania.
    """
    chybaju = tuple(sorted(set(disk) - set(indexed)))
    osirele = tuple(sorted(set(indexed) - set(disk)))

    zastarale = []
    for oznacenie in sorted(set(disk) & set(indexed)):
        zaindexovane = indexed[oznacenie]
        if zaindexovane.tzinfo is None:
            zaindexovane = zaindexovane.replace(tzinfo=timezone.utc)
        if disk[oznacenie] > zaindexovane.timestamp() + STALE_TOLERANCE_SECONDS:
            zastarale.append(oznacenie)

    return KbIndexComparison(
        on_disk=len(disk),
        indexed=len(indexed),
        missing=chybaju,
        stale=tuple(zastarale),
        orphaned=osirele,
        last_indexed_at=max(indexed.values()) if indexed else None,
    )


def _indexovatelny(oznacenie: str) -> bool:
    """Patrí tento dokument do korpusu vôbec?

    Kategórie ako ``credentials`` sa indexovať NESMÚ (indexer ich odmieta). Keby sa počítali medzi
    chýbajúce, údaj „nesedí N súborov" by nikdy nespadol na nulu a prestal by niečo znamenať.
    Kategóriu určuje ``RAGIndexer._extract_category`` — jeden zdroj, nie druhá kópia pravidla.
    """
    return RAGIndexer._extract_category(oznacenie).lower() not in NEVER_INDEX_CATEGORIES


def scan_disk(root: Optional[Path] = None) -> dict[str, float]:
    """Označenie → čas poslednej zmeny, pre každý ``.md`` v Znalostnej báze."""
    koren = Path(root or settings.knowledge_base_path)
    if not koren.is_dir():
        raise KbIndexUnavailable(f"Znalostná báza nie je dostupná: {koren}")

    najdene: dict[str, float] = {}
    for cesta in koren.rglob("*.md"):
        if not cesta.is_file():
            continue
        if any(zlozka in {".git", "node_modules"} for zlozka in cesta.relative_to(koren).parts[:-1]):
            continue
        oznacenie = str(cesta.relative_to(koren)).replace("\\", "/")
        if not _indexovatelny(oznacenie):
            continue
        najdene[oznacenie] = cesta.stat().st_mtime
    return najdene


def scan_index(tenant: str = TENANT) -> dict[str, datetime]:
    """Označenie → čas zaindexovania (najnovší kúsok dokumentu)."""
    try:
        client = RAGIndexer()._get_client()
        zaznamy: dict[str, datetime] = {}
        offset = None
        while True:
            body, offset = client.scroll(
                collection_name=tenant,
                limit=1000,
                offset=offset,
                with_payload=["source_file", "ingested_at"],
                with_vectors=False,
            )
            for bod in body:
                payload = bod.payload or {}
                oznacenie = payload.get("source_file")
                kedy = payload.get("ingested_at")
                if not oznacenie or not kedy:
                    continue
                try:
                    cas = datetime.fromisoformat(str(kedy))
                except ValueError:
                    continue
                if cas.tzinfo is None:
                    cas = cas.replace(tzinfo=timezone.utc)
                if oznacenie not in zaznamy or cas > zaznamy[oznacenie]:
                    zaznamy[oznacenie] = cas
            if offset is None:
                break
        return zaznamy
    except KbIndexUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - sieťové zlyhania sa nedajú vyvolať deterministicky
        raise KbIndexUnavailable(f"RAG index nie je dostupný: {exc}") from exc


def status(root: Optional[Path] = None, tenant: str = TENANT) -> KbIndexComparison:
    """Živé porovnanie disku s indexom. Nič neukladá a nič neopravuje."""
    return compare(scan_disk(root), scan_index(tenant))


async def sync_once(
    root: Optional[Path] = None,
    tenant: str = TENANT,
    *,
    limit: int = MAX_DOCUMENTS_PER_PASS,
) -> dict:
    """Dorovná rozdiel: doindexuje chýbajúce a zastarané, odstráni osirelé. Vráti, čo spravila.

    Poradie je zámerné — najprv **chýbajúce**, potom **zastarané**: dokument, ktorý v indexe nie je
    vôbec, je horší než dokument, ktorý je tam starý. Osirelé sa mažú vždy, lebo mazanie je lacné
    (bez embeddingov) a odpoveď z dokumentu, ktorý už neexistuje, je najzavádzajúcejšia zo všetkých.
    """
    rozdiel = status(root, tenant)
    koren = Path(root or settings.knowledge_base_path)
    indexer = RAGIndexer()

    zmazane = 0
    for oznacenie in rozdiel.orphaned:
        try:
            zmazane += await indexer.delete_document(source_file=oznacenie, tenant=tenant)
        except Exception:
            logger.exception("KB sync: nepodarilo sa odstrániť osirelý dokument %s", oznacenie)

    zaindexovane, zlyhane = 0, 0
    for oznacenie in (*rozdiel.missing, *rozdiel.stale)[:limit]:
        try:
            await indexer.index_document(str(koren / oznacenie), tenant=tenant, source_file=oznacenie)
            zaindexovane += 1
        except Exception:
            zlyhane += 1
            logger.exception("KB sync: nepodarilo sa zaindexovať %s", oznacenie)

    zostava = max(0, len(rozdiel.missing) + len(rozdiel.stale) - zaindexovane - zlyhane)
    if zostava:
        # Strop sa NIKDY nesmie tváriť ako hotovo — inak by tichý rozchod nahradil iný tichý rozchod.
        logger.info("KB sync: hotovo %d, zlyhalo %d, do ďalšieho prechodu zostáva %d", zaindexovane, zlyhane, zostava)
    else:
        logger.info("KB sync: hotovo %d, zlyhalo %d, odstránených osirelých kúskov %d", zaindexovane, zlyhane, zmazane)

    return {
        "indexed": zaindexovane,
        "failed": zlyhane,
        "orphaned_chunks_removed": zmazane,
        "remaining": zostava,
    }
