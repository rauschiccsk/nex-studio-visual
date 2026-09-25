"""Nasadenie po sebe upratá na serveri zákazníka — a len keď prebehlo (ICCINT-154).

**Čo tomu predchádzalo.** Odkedy kokpit nasadzuje na cudzí stroj, obrazy sa stavajú tam. Po každej
stavbe zostáva vyrovnávacia pamäť. 25.09.2026 zazvonil hlásič porúch MAGERa — na koreňovom disku
zostávalo 14 % (hranica 15 %). Zmerané:

    /dev/sda2   97,9 GB celkom   81,6 GB použité   11,3 GB voľné   (88 %)
    z toho vyrovnávacia pamäť stavby: 12,2 GB — za deň a pol, za dve nasadenia

Director na návrh, že by to upratoval človek: *„to znamená, že po nasadení novej verzie Máger Dedo
bude musieť vždy upratať niečo?"* Nemá. Je to chýbajúci krok v nasadzovaní.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: po ZLYHANÍ sa neupratuje (cesta späť je dôležitejšia než
miesto), pripnuté obrazy zostávajú (sú tou cestou späť), pri inštalácii na tomto stroji sa neupratuje
nič, a zlyhanie upratovania nesmie zhodiť nasadenie — appka beží, len disku sa neuľavilo.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services import orchestrator as O


class _FalosnyProces:
    """Náhrada spusteného príkazu — vráti pripravený výstup a návratový kód."""

    def __init__(self, vystup: bytes = b"", returncode: int = 0):
        self._vystup = vystup
        self.returncode = returncode

    async def communicate(self):
        return self._vystup, b""


def _zachyt(monkeypatch, *, vystup: bytes = b"Total reclaimed space: 12.15GB\n", returncode: int = 0):
    """Zapamätá si spustené príkazy a prostredie; nič sa naozaj nespustí."""
    videne: list[dict] = []

    async def _fake(*cmd, **kw):
        videne.append({"cmd": list(cmd), "env": kw.get("env") or {}})
        return _FalosnyProces(vystup, returncode)

    monkeypatch.setattr(O.asyncio, "create_subprocess_exec", _fake)
    return videne


# ── 1. po úspechu sa upratá ───────────────────────────────────────────────────


def test_a_successful_deploy_cleans_the_build_cache_on_the_target(monkeypatch) -> None:
    """⚠️ Jadro veci: bez tohto rastie disk zákazníka po každom nasadení a nikto to nezastaví."""
    videne = _zachyt(monkeypatch)

    detail = asyncio.run(O._uprac_na_cieli(True, "OK", "mager"))

    prikazy = [" ".join(v["cmd"]) for v in videne]
    assert any("builder prune" in p for p in prikazy), "vyrovnávacia pamäť sa neupratala"
    assert all(v["env"].get("DOCKER_HOST") == "ssh://mager" for v in videne), "upratovalo sa u nás"
    assert "Upratané na cieli" in detail and "12.15GB" in detail


def test_the_freed_space_is_reported(monkeypatch) -> None:
    """Koľko sa uvoľnilo, patrí do hlásenia — inak je to neviditeľná zmena na cudzom stroji."""
    _zachyt(monkeypatch, vystup=b"Total reclaimed space: 3.2GB\n")

    detail = asyncio.run(O._uprac_na_cieli(True, "OK", "mager"))

    assert "3.2GB" in detail


# ── 2. pripnuté obrazy sú cesta späť a zostávajú ──────────────────────────────


def test_pinned_images_are_never_removed(monkeypatch) -> None:
    """⚠️ Obraz predchádzajúcej verzie je NÁVRAT. 25.09.2026 sa vďaka nemu dal vrátiť stav, keď predpis
    na MAGERi ukazoval na verziu, ktorá nevznikla. ``image prune -a`` by ho zmazal."""
    videne = _zachyt(monkeypatch)

    asyncio.run(O._uprac_na_cieli(True, "OK", "mager"))

    prikazy = [" ".join(v["cmd"]) for v in videne]
    assert any("image prune -f" in p for p in prikazy), "nepoužívané vrstvy sa neupratali"
    assert not any("image prune -a" in p or "image prune -af" in p for p in prikazy), (
        "zmazali by sa aj pripnuté obrazy — teda cesta späť"
    )
    assert not any("system prune" in p for p in prikazy), "system prune siaha aj na zväzky s dátami"


# ── 3. po zlyhaní a doma sa neupratuje ────────────────────────────────────────


def test_a_failed_deploy_cleans_nothing(monkeypatch) -> None:
    """⚠️ Vtedy je cesta späť dôležitejšia než miesto — a práve staršie obrazy sú tou cestou."""
    videne = _zachyt(monkeypatch)

    detail = asyncio.run(O._uprac_na_cieli(False, "deploy zlyhal", "mager"))

    assert videne == [], "po zlyhaní sa upratovalo"
    assert detail == "deploy zlyhal"


def test_an_instance_on_this_machine_is_not_cleaned(monkeypatch) -> None:
    """Tunajší disk má vlastnú správu; nasadenie doň nehovorí."""
    videne = _zachyt(monkeypatch)

    detail = asyncio.run(O._uprac_na_cieli(True, "OK", None))

    assert videne == [] and detail == "OK"


# ── 4. upratovanie nikdy nezhodí nasadenie ────────────────────────────────────


def test_a_failed_cleanup_does_not_fail_the_deploy(monkeypatch) -> None:
    """Appka beží, len disku sa neuľavilo. Zhodiť kvôli tomu nasadenie by bolo neúmerné."""
    _zachyt(monkeypatch, returncode=1)

    detail = asyncio.run(O._uprac_na_cieli(True, "OK", "mager"))

    assert detail.startswith("OK"), "hlásenie o úspechu sa stratilo"
    assert "nepodarilo" in detail, "zlyhanie upratovania sa zamlčalo"


def test_a_stuck_cleanup_is_not_waited_out_forever(monkeypatch) -> None:
    """Údržba nesmie držať nasadenie. Keď sa nestihne, povie sa to a ide sa ďalej."""

    class _ViaciacProces:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(3600)  # čaká sa na VÝSTUP, nie na spustenie — strop je tam

    async def _visi(*_cmd, **_kw):
        return _ViaciacProces()

    monkeypatch.setattr(O.asyncio, "create_subprocess_exec", _visi)
    monkeypatch.setattr(O, "CLEANUP_TIMEOUT", 0.05)

    detail = asyncio.run(O._uprac_na_cieli(True, "OK", "mager"))

    assert detail.startswith("OK") and "nepodarilo" in detail


@pytest.mark.parametrize("ciel", ["", "   ", None])
def test_a_blank_target_means_this_machine(monkeypatch, ciel) -> None:
    """Prázdny cieľ nie je cudzí stroj — a nesmie sa tak čítať."""
    videne = _zachyt(monkeypatch)

    asyncio.run(O._uprac_na_cieli(True, "OK", ciel))

    assert videne == []
