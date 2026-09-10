"""Preindexovanie maže a zapisuje TEN ISTÝ dokument (ICCINT-98).

**Čo sa dialo.** ``reindex_document`` dostáva ``source_file`` — označenie dokumentu v korpuse — a
najprv podľa neho staré kúsky zmaže. Potom však volalo ``index_document`` **bez neho**, a tá si
označenie odvodila z ``file_path``. Keď sa tie dve líšili (bežne: mazanie podľa cesty v rámci bázy,
zápis podľa absolútnej cesty na disku), **nezmazalo sa nič a pribudla druhá kópia** toho istého
dokumentu. Vyhľadávanie potom vracia ten istý text niekoľkokrát a staré znenie žije ďalej vedľa nového.

Zmerané 09.09.2026 naživo: volanie s absolútnou cestou zapísalo 4 body s kategóriou ``home``, kým
zvyšných 3 720 bodov v kolekcii používa tvar ``projects/…``. Mazanie ohlásilo „no points found“.

**Prečo tento tvar stráže.** Čisté funkcie samy nález nechytia — chyba nebola vo výpočte označenia,
ale v tom, že sa jedno z dvoch miest naň nepozrelo. Preto sa tu pozeráme na to, čo išlo do Qdrantu:
podľa čoho sa mazalo a s čím sa zapísalo. Parameter, ktorý funkcia prijme a nepoužije, je horší než
žiadny — sľubuje niečo, čo nerobí.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.rag.indexer import DocumentMustNotBeIndexed, RAGIndexer

V_BAZE = "projects/nex-studio-visual/NAVOD.md"
NA_DISKU = "/home/icc/knowledge/projects/nex-studio-visual/NAVOD.md"
#: Súbor, ktorý sa číta, ale v korpuse sa NEVOLÁ tak ako na disku — napríklad odložený nahraný obsah.
#: Práve na tento prípad ``source_file`` existuje: bez neho by sa dokument zapísal pod menom odkladiska.
#:
#: ⚠️ Prvé znenie tejto stráže tu malo absolútnu cestu k tomu istému dokumentu — lenže tú
#: :meth:`_document_id` skráti na to isté označenie, takže test prešiel aj s vypnutou opravou.
#: Rozdiel, ktorý sa normalizáciou zotrie, nemeria nič.
ODKLADISKO = "/var/tmp/nahrane-12345.md"


class _FakeQdrant:
    """Atrapa Qdrantu, ktorá si pamätá, podľa čoho sa mazalo a čo sa zapísalo."""

    def __init__(self) -> None:
        self.deleted_by: list[str] = []
        self.upserted: list[dict[str, Any]] = []

    @staticmethod
    def _source_of(count_filter: Any) -> str:
        return count_filter.must[0].match.value

    def count(self, collection_name: str, count_filter: Any, exact: bool = True) -> Any:
        # „Niečo tam je“ — inak sa mazanie skratkou skončí a nedozvieme sa, podľa čoho mazalo.
        return SimpleNamespace(count=3)

    def delete(self, collection_name: str, points_selector: Any) -> None:
        self.deleted_by.append(self._source_of(points_selector.filter))

    def upsert(self, collection_name: str, points: list) -> None:
        self.upserted.extend(p.payload for p in points)


@pytest.fixture()
def indexer_a_qdrant(monkeypatch) -> tuple[RAGIndexer, _FakeQdrant]:
    idx = RAGIndexer()
    fake = _FakeQdrant()
    monkeypatch.setattr(idx, "_get_client", lambda: fake)

    async def _embedding(_text: str) -> list[float]:
        return [0.0] * 8

    monkeypatch.setattr(idx, "_get_embedding", _embedding)
    return idx, fake


@pytest.mark.asyncio
async def test_reindex_deletes_and_writes_under_the_same_name(indexer_a_qdrant) -> None:
    """⚠️ Jadro nálezu: čo sa zmazalo a čo sa zapísalo, musí byť ten istý dokument."""
    idx, qdrant = indexer_a_qdrant

    await idx.reindex_document(
        file_path=ODKLADISKO,  # odkiaľ sa obsah číta
        tenant="icc",
        source_file=V_BAZE,  # ako sa dokument v korpuse volá — LÍŠIA sa, a práve o to ide
        content="# Nadpis\n\nTelo dokumentu.\n",
    )

    zapisane = {p["source_file"] for p in qdrant.upserted}
    assert zapisane == {V_BAZE}, (
        f"zapísalo sa pod {zapisane}, mazalo sa podľa {set(qdrant.deleted_by)} — dokument je v korpuse dvakrát"
    )
    assert set(qdrant.deleted_by) == zapisane, "mazanie a zápis sa rozišli v označení dokumentu"


@pytest.mark.asyncio
async def test_an_absolute_path_alone_still_names_the_document_the_same_way(indexer_a_qdrant) -> None:
    """Aj bez ``source_file`` musí absolútna cesta viesť na to isté označenie — inak vznikne dvojička.

    Toto je presne to volanie, ktorým sa nález našiel.
    """
    idx, qdrant = indexer_a_qdrant

    await idx.index_document(file_path=NA_DISKU, tenant="icc", content="# Nadpis\n\nTelo.\n")

    assert {p["source_file"] for p in qdrant.upserted} == {V_BAZE}
    assert {p["category"] for p in qdrant.upserted} == {"projects"}, (
        "kategória sa určila z absolútnej cesty — filtrovanie podľa kategórie taký dokument nenájde"
    )


@pytest.mark.asyncio
async def test_reindexing_twice_does_not_leave_two_copies(indexer_a_qdrant) -> None:
    """Druhé preindexovanie musí to prvé zmazať, nie pribudnúť vedľa neho.

    Bez tohto tvrdenia by stráže vyššie prešli aj vtedy, keby sa mazalo správne, ale zbytočne —
    tu sa meria to, čo Directora naozaj bolí: koľko kópií po dvoch behoch v korpuse ostane.
    """
    idx, qdrant = indexer_a_qdrant

    for _ in range(2):
        await idx.reindex_document(file_path=ODKLADISKO, tenant="icc", source_file=V_BAZE, content="# A\n\nB.\n")

    assert qdrant.deleted_by, "druhý beh nič nezmazal — v korpuse by ostali dve kópie"
    assert set(qdrant.deleted_by) == {V_BAZE}


# --- prístupové údaje sa do korpusu nedostanú (ICCINT-99) ---------------------


@pytest.mark.asyncio
async def test_a_credentials_document_is_refused_before_anything_is_written(indexer_a_qdrant) -> None:
    """⚠️ Obrana patrí na vstup, nie až na výstup.

    Vyhľadávanie aj čítanie dokumentu kategóriu ``credentials`` bežným kontám skrývajú — lenže to je
    clona pred niečím, čo v korpuse UŽ leží, a leží tam ticho: v prehliadači súborov ten dokument nikto
    nevidí. Zmerané 10.09.2026: dva kúsky ``credentials/CREDENTIALS.md`` boli v kolekcii ``icc``, hoci
    priečinok na disku už dávno neexistuje. Kópia prežila zmazanie originálu.
    """
    idx, qdrant = indexer_a_qdrant

    with pytest.raises(DocumentMustNotBeIndexed):
        await idx.index_document(file_path="credentials/CREDENTIALS.md", tenant="icc", content="# čokoľvek\n")

    assert qdrant.upserted == [], "do korpusu sa aj tak niečo zapísalo"
    assert qdrant.deleted_by == [], "dokument sa ani nemá začať spracúvať"


@pytest.mark.asyncio
async def test_the_absolute_path_does_not_smuggle_it_in(indexer_a_qdrant) -> None:
    """Iný tvar tej istej cesty nesmie zábranu obísť — preto sa kontroluje až odvodené označenie."""
    idx, qdrant = indexer_a_qdrant

    with pytest.raises(DocumentMustNotBeIndexed):
        await idx.index_document(
            file_path="/home/icc/knowledge/credentials/CREDENTIALS.md", tenant="icc", content="# čokoľvek\n"
        )

    assert qdrant.upserted == []


@pytest.mark.asyncio
async def test_an_ordinary_document_still_goes_in(indexer_a_qdrant) -> None:
    """Zábrana sa smie týkať iba tých kategórií — inak by sa Znalostná báza prestala indexovať celá.

    Bez tohto tvrdenia by stráže vyššie prešli aj vtedy, keby indexovanie odmietalo všetko.
    """
    idx, qdrant = indexer_a_qdrant

    await idx.index_document(file_path="projects/x/README.md", tenant="icc", content="# A\n\nB.\n")

    assert qdrant.upserted, "bežný dokument sa nezaindexoval"


def test_what_is_hidden_from_reading_is_also_never_indexed() -> None:
    """Dve rozhodnutia o tej istej kategórii sa nesmú rozísť tým nebezpečným smerom.

    Keby niekto pridal kategóriu medzi skryté pri čítaní a zabudol na indexovanie, jej obsah by sa do
    prehľadávateľného úložiska ďalej zapisoval — a clona pri čítaní by len zakrývala, že tam je.
    """
    from backend.api.routes.knowledge import _RESTRICTED_CATEGORIES
    from backend.rag.indexer import NEVER_INDEX_CATEGORIES

    zabudnute = {c.lower() for c in _RESTRICTED_CATEGORIES} - {c.lower() for c in NEVER_INDEX_CATEGORIES}
    assert not zabudnute, (
        f"kategórie {sorted(zabudnute)} sa pri čítaní skrývajú, ale do indexu sa zapisujú — "
        "clona pri čítaní nie je obrana, keď obsah v úložisku už leží"
    )
