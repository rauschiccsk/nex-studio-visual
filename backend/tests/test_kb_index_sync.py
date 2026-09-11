"""Znalostná báza ↔ RAG index (ICCINT-111).

Zmerané 10.09.2026: z 205 súborov Znalostnej bázy 71 v indexe nebolo vôbec a 42 bolo starších než
disk — 113 z 205 nesedelo. Indexer existoval, ale nespúšťalo ho NIČ, a návod bol kruhový, takže sa
povinnosť nedala ani nájsť, nieto splniť.

Najcennejšia skúška tu nie je tá o počítaní rozdielov. Je to tá, ktorá stráži, že **„neviem" sa
nevydáva za „v poriadku"**: nula rozdielov z nedostupného zdroja vyzerá presne ako poriadok a bola by
horšia než pôvodná chyba — tichý rozchod by nahradil zelený údaj, ktorý netvrdí nič.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services import kb_index_sync as kb


def _cas(**kw) -> datetime:
    return datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc) + timedelta(**kw)


# --- čisté rozhodovanie -----------------------------------------------------------------------


def test_everything_matching_reads_as_in_sync():
    porovnanie = kb.compare({"icc/A.md": _cas().timestamp()}, {"icc/A.md": _cas(minutes=1)})
    assert porovnanie.in_sync
    assert porovnanie.out_of_sync == 0


def test_a_file_never_indexed_is_missing():
    porovnanie = kb.compare({"icc/STRUCTURE.md": _cas().timestamp()}, {})
    assert porovnanie.missing == ("icc/STRUCTURE.md",)
    assert not porovnanie.in_sync


def test_a_file_changed_after_indexing_is_stale():
    """Presne NEX Inbox: v indexe je, ale jeho podoba je o dva mesiace staršia než disk."""
    porovnanie = kb.compare(
        {"projects/nex-inbox/STATUS.md": _cas(days=63).timestamp()},
        {"projects/nex-inbox/STATUS.md": _cas()},
    )
    assert porovnanie.stale == ("projects/nex-inbox/STATUS.md",)
    assert not porovnanie.in_sync


def test_a_file_deleted_from_disk_is_orphaned():
    """Odpoveď z dokumentu, ktorý už neexistuje, je najzavádzajúcejšia zo všetkých."""
    porovnanie = kb.compare({}, {"projects/zrusene/X.md": _cas()})
    assert porovnanie.orphaned == ("projects/zrusene/X.md",)
    assert not porovnanie.in_sync


def test_a_file_indexed_seconds_after_it_was_written_is_not_stale():
    """Bez tolerancie by KAŽDÝ súbor vyzeral zastaraný hneď po zápise — index vzniká o chvíľu neskôr,
    takže by údaj „nesedí N" nikdy nespadol na nulu a prestal by niečo znamenať."""
    porovnanie = kb.compare({"icc/A.md": _cas(seconds=5).timestamp()}, {"icc/A.md": _cas()})
    assert porovnanie.in_sync, "pár sekúnd rozdielu sa vydáva za zastaranie"


def test_the_newest_write_across_the_corpus_is_reported():
    porovnanie = kb.compare({}, {"a.md": _cas(), "b.md": _cas(hours=3), "c.md": _cas(hours=-1)})
    assert porovnanie.last_indexed_at == _cas(hours=3)


def test_an_empty_index_reports_no_last_write_instead_of_a_made_up_one():
    assert kb.compare({}, {}).last_indexed_at is None


# --- čo do korpusu nepatrí --------------------------------------------------------------------


def test_credentials_are_not_counted_as_missing(tmp_path):
    """Tajomstvá sa indexovať NESMÚ. Keby sa rátali medzi chýbajúce, „nesedí N" by nikdy nespadlo
    na nulu — a údaj, ktorý sa nedá dosiahnuť, prestane niekto sledovať."""
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "tajne.md").write_text("x", encoding="utf-8")
    (tmp_path / "icc").mkdir()
    (tmp_path / "icc" / "A.md").write_text("x", encoding="utf-8")

    najdene = kb.scan_disk(tmp_path)

    assert "icc/A.md" in najdene
    assert not any(o.startswith("credentials/") for o in najdene), "tajomstvá sa dostali do porovnania"


def test_git_internals_are_not_part_of_the_corpus(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG.md").write_text("x", encoding="utf-8")
    (tmp_path / "icc").mkdir()
    (tmp_path / "icc" / "A.md").write_text("x", encoding="utf-8")

    assert set(kb.scan_disk(tmp_path)) == {"icc/A.md"}


# --- „neviem" sa nevydáva za „v poriadku" -----------------------------------------------------


def test_an_unreachable_knowledge_base_raises_instead_of_reading_as_clean(tmp_path):
    """Toto je tá najdôležitejšia. Nula rozdielov z nedostupného zdroja vyzerá presne ako poriadok."""
    with pytest.raises(kb.KbIndexUnavailable):
        kb.scan_disk(tmp_path / "tento-priecinok-neexistuje")


def test_an_unreachable_index_raises_instead_of_reading_as_clean(monkeypatch):
    """Druhá polovica tej istej zásady — keď mlčí Qdrant, nesmie z toho vyjsť zelený údaj."""

    class MrtvyKlient:
        def scroll(self, **_kw):
            raise ConnectionError("Qdrant neodpovedá")

    monkeypatch.setattr(kb.RAGIndexer, "_get_client", lambda self: MrtvyKlient())

    with pytest.raises(kb.KbIndexUnavailable):
        kb.scan_index()


# --- INCIDENT 11.09.2026: dorovnanie vymazalo ostrý index ---------------------------------------
#
# O 07:42 som na hostiteľovi pustil testy. ``backend/tests/conftest.py`` robí ``with TestClient(app)``,
# čo spustí SKUTOČNÝ životný cyklus appky — a v ňom moju novú slučku. ``tests/conftest.py:447`` pritom
# prepína ``settings.knowledge_base_path`` na dočasný priečinok. Slučka sa teda pozrela na prázdny
# strom, usúdila, že všetkých ~150 dokumentov v indexe je osirelých, a zmazala ich z OSTRÉHO Qdrantu:
# 3718 bodov → 293.
#
# Nič sa nestratilo natrvalo (zdrojom pravdy je disk, index sa prestaval), ale chyba nebola v testoch.
# Chyba bola v návrhu: zásadu „neviem sa nevydáva za v poriadku" som uplatnil na ČÍTANIE stavu a nie
# na MAZANIE. Prázdna alebo neúplná strana disku nesmie nikdy oprávniť vyprázdnenie korpusu — ani
# v testoch, ani keby sa Znalostná báza raz nepripojila v ostrej prevádzke.


def test_an_empty_disk_never_authorises_deleting_the_corpus():
    """Presne ten incident: disk prázdny, index plný. Odpoveď nesmie byť „zmaž všetko"."""
    index = {f"icc/{i}.md": _cas() for i in range(150)}
    assert not kb.deletion_is_safe(kb.compare({}, index)), "prázdny disk dostal povolenie vyprázdniť index"


def test_a_disk_that_lost_most_of_the_corpus_is_treated_as_broken_not_as_intent():
    """Aj čiastočne zlá cesta je zlá cesta. Že by niekto naozaj zmazal 80 % Znalostnej bázy medzi
    dvoma prechodmi, je neporovnateľne menej pravdepodobné než zle pripojený priečinok."""
    index = {f"icc/{i}.md": _cas() for i in range(100)}
    disk = {f"icc/{i}.md": _cas().timestamp() for i in range(20)}
    assert not kb.deletion_is_safe(kb.compare(disk, index))


def test_a_handful_of_genuinely_deleted_documents_is_still_cleaned_up():
    """Druhý smer — bez neho by stačilo mazanie vypnúť a skúšky vyššie by prešli. Osirelé dokumenty
    sa MUSIA odstraňovať: odpoveď z dokumentu, ktorý už neexistuje, je najzavádzajúcejšia zo všetkých."""
    index = {f"icc/{i}.md": _cas() for i in range(100)}
    disk = {f"icc/{i}.md": _cas().timestamp() for i in range(97)}
    assert kb.deletion_is_safe(kb.compare(disk, index)), "bežné upratovanie sa zablokovalo"


def test_an_empty_index_is_safe_to_touch():
    """Prázdny index nie je čo chrániť — a keby sa bral ako podozrivý, prvé naplnenie by sa zablokovalo."""
    assert kb.deletion_is_safe(kb.compare({"icc/A.md": _cas().timestamp()}, {}))
